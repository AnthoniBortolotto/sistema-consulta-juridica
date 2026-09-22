"""CLI do eval: `python -m consulta_juridica.eval <modo>`.

    python -m consulta_juridica.eval recuperacao   # grátis, roda o tempo todo
    python -m consulta_juridica.eval e2e           # gasta tokens

É borda: junto com a API e o CLI da ingestão, um dos poucos lugares que leem `Settings` e
constroem dependências.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from ..config import obter_settings
from ..retrieval.expansao import Nivel
from ..store import db
from ..tls import confiar_no_sistema
from .golden import carregar, validar
from .run import RelatorioRecuperacao, avaliar_recuperacao

GOLDEN_PADRAO = Path("golden/seed.jsonl")


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m consulta_juridica.eval",
        description="Mede a recuperação contra o golden set. Não chama LLM no modo "
        "`recuperacao`.",
    )
    sub = p.add_subparsers(dest="modo", required=True)

    v = sub.add_parser("validar", help="confere o golden contra o corpus, sem medir nada")
    r = sub.add_parser("recuperacao", help="recall@k e MRR, sem gastar token")
    e = sub.add_parser("e2e", help="resposta e citação — gasta tokens")

    for s in (v, r, e):
        s.add_argument("--golden", type=Path, default=GOLDEN_PADRAO)

    r.add_argument(
        "--k",
        type=int,
        action="append",
        default=None,
        help="k do recall@k; repetível (default: 5 10 20)",
    )
    r.add_argument("--busca", type=int, default=None, help="candidatos da fusão RRF")
    r.add_argument(
        "--nivel", default=None, choices=[n.value for n in Nivel], help="até onde expandir"
    )
    r.add_argument(
        "--sem-rerank",
        action="store_true",
        help="reranker identidade: mede a fusão pura, e é assim que se atribui o ganho "
        "do cross-encoder",
    )
    r.add_argument("--threads", type=int, default=os.cpu_count())
    r.add_argument("--json", action="store_true", help="relatório completo em JSON")

    e.add_argument(
        "--confirmar",
        action="store_true",
        help="executa e GASTA. Sem isto, só estima o custo e sai — o que já está no cache "
        "sai de graça, o resto é cobrado",
    )
    e.add_argument("--threads", type=int, default=os.cpu_count())
    e.add_argument("--json", action="store_true", help="relatório completo em JSON")
    return p


def _validar(args, cfg) -> int:
    itens = carregar(args.golden)
    conn = db.conectar(cfg.caminho_sqlite, somente_leitura=True)
    try:
        problemas = validar(itens, conn)
    finally:
        conn.close()
    print(f"{len(itens)} itens em {args.golden}")
    for p in problemas:
        print(f"  {p}", file=sys.stderr)
    print(f"{len(problemas)} problemas" if problemas else "golden íntegro contra o corpus")
    return 1 if problemas else 0


def _recuperacao(args, cfg) -> int:
    from ..service import construir_recuperador

    itens = carregar(args.golden)
    ks = tuple(sorted(args.k or (5, 10, 20)))

    print(f"carregando modelos (rerank={'não' if args.sem_rerank else 'sim'})…", flush=True)
    rec = construir_recuperador(
        cfg, com_rerank=not args.sem_rerank, threads=args.threads
    )
    if args.nivel:
        rec.nivel = Nivel(args.nivel)

    # O golden é validado antes de medir: um esperado que não existe no corpus derruba o
    # recall em silêncio e faz parecer defeito da recuperação.
    problemas = validar(itens, rec.conn)
    for p in problemas:
        print(f"golden: {p}", file=sys.stderr)

    try:
        rel = avaliar_recuperacao(
            rec, itens, ks, k_busca=args.busca or cfg.k_busca, estrategia_chunk=cfg.estrategia_chunk
        )
    finally:
        rec.conn.close()

    if args.json:
        print(json.dumps(dataclasses.asdict(rel), ensure_ascii=False, indent=2, default=str))
    else:
        _imprimir(rel, ks)
    return 0


def _imprimir(rel: RelatorioRecuperacao, ks: tuple[int, ...]) -> None:
    print(
        f"\nchunk={rel.estrategia_chunk}  rerank={rel.reranker}  "
        f"expansão={rel.nivel_expansao}  k_busca={rel.k_busca}"
    )
    print(
        f"{rel.n_itens} perguntas medidas ({rel.n_abstencao} de abstenção fora da média), "
        f"{rel.duracao_s:.1f}s"
    )

    cols = "  ".join(f"recall@{k}" for k in ks)
    print(f"\n{'':22} {cols}   MRR")
    print(f"{'TOTAL':22} {_linha(rel, ks)}")
    for mec, sub in rel.por_mecanismo.items():
        print(f"{mec + ' (' + str(sub.n_itens) + ')':22} {_linha(sub, ks)}")

    print("\npor pergunta:")
    for r in rel.itens:
        posicao = f"#{r.posicao}" if r.posicao else "—"
        falta = f"  falta: {', '.join(r.faltando)}" if r.faltando else ""
        print(f"  {r.id:8} {posicao:>4}  {r.pergunta[:58]:58}{falta}")


def _linha(rel: RelatorioRecuperacao, ks: tuple[int, ...]) -> str:
    valores = "  ".join(f"{rel.recall.get(k, 0.0):>9.3f}" for k in ks)
    return f"{valores}   {rel.mrr:.3f}"


def _e2e(args, cfg) -> int:
    from ..service import construir_backend, construir_servico
    from .run import avaliar_ponta_a_ponta, estimar_ponta_a_ponta

    # Antes de carregar ~26 s de modelo: o backend sem citations é recusado de qualquer
    # jeito, e descobrir isso depois da carga é desperdício.
    if not construir_backend(cfg).suporta_citacoes:
        print(
            f"o backend {cfg.backend_llm!r} não tem citations nativas; a acurácia de citação "
            "medida por ele não significa nada. Use CJ_BACKEND_LLM=api.",
            file=sys.stderr,
        )
        return 1

    itens = carregar(args.golden)
    print("carregando modelos…", flush=True)
    svc = construir_servico(cfg, threads=args.threads)
    try:
        for p in validar(itens, svc.recuperador.conn):
            print(f"golden: {p}", file=sys.stderr)

        est = estimar_ponta_a_ponta(svc, itens)
        print()
        print(
            f"{est.n_itens} perguntas: {est.do_cache} no cache, "
            f"{est.sem_candidatos} sem trecho (grátis), {est.a_pagar} a pagar"
        )
        if est.a_pagar:
            faixa = (
                f"US$ {est.custo_min_usd:.2f} a {est.custo_max_usd:.2f}"
                if est.custo_min_usd is not None
                else "desconhecido (modelo fora da tabela de preços)"
            )
            print(f"  ~{est.tokens_entrada:,} tokens de entrada em {est.modelo}: {faixa}")
            print("  (estimativa grosseira: a saída depende de quanto o modelo pensa)")

        if not args.confirmar:
            print()
            print("nada foi gasto. Rode de novo com --confirmar para executar.")
            return 0

        rel = avaliar_ponta_a_ponta(svc, itens)
    finally:
        svc.recuperador.conn.close()

    if args.json:
        print(json.dumps(dataclasses.asdict(rel), ensure_ascii=False, indent=2, default=str))
    else:
        _imprimir_e2e(rel)
    return 0


def _imprimir_e2e(rel) -> None:
    custo = (
        f"US$ {rel.custo_estimado_usd:.2f}"
        if rel.custo_estimado_usd is not None
        else "desconhecido"
    )
    print()
    print(f"backend={rel.backend}  modelo={rel.modelo}  prompt={rel.versao_prompt}")
    print(
        f"{rel.n_itens} perguntas em {rel.duracao_s:.0f}s, "
        f"{rel.chamadas_pagas} chamadas pagas, {custo}"
    )
    print()
    print(f"  acurácia de citação   {rel.acuracia_citacao:.3f}")
    print(f"  abstenção correta     {rel.abstencao_correta:.3f}   (das que deviam)")
    print(f"  abstenção indevida    {rel.abstencao_indevida:.3f}   (das que não deviam)")
    print(f"  taxa de abstenção     {rel.taxa_abstencao:.3f}")
    print(f"  respostas sem citação {rel.sem_citacao}")

    print()
    print("por pergunta:")
    for r in rel.itens:
        if r.abstencao:
            situacao = f"absteve ({r.abstencao})" + (" ✓" if r.deve_abster else " ✗")
        elif r.deve_abster:
            situacao = "RESPONDEU quando devia se abster ✗"
        else:
            situacao = f"{r.corretas}/{len(r.citados)} citações corretas"
        print(f"  {r.id:8} {situacao}")
        for c in r.citados:
            print(f"             {c}")


_MODOS = {"validar": _validar, "recuperacao": _recuperacao, "e2e": _e2e}


def main(argv: list[str] | None = None) -> int:
    confiar_no_sistema()
    args = construir_parser().parse_args(argv)
    return _MODOS[args.modo](args, obter_settings())


if __name__ == "__main__":
    raise SystemExit(main())
