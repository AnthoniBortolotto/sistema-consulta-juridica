"""Inspeção da recuperação sem gastar token.

    python -m consulta_juridica.retrieval "prazo para contestação" --data 2026-09-20

Imprime os trechos recuperados com os scores de fusão e de rerank. A maior parte da
iteração do projeto acontece aqui, não na geração: recall@k se mede sem chamar o modelo.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date

from ..config import obter_settings
from ..models import TipoDispositivo, Trecho
from ..tls import confiar_no_sistema
from .expansao import Nivel
from .filtros import Criterios


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m consulta_juridica.retrieval",
        description="Recupera trechos para uma pergunta. Não chama nenhum LLM.",
    )
    p.add_argument("pergunta")
    p.add_argument(
        "--data",
        type=date.fromisoformat,
        required=True,
        help="data de referência (AAAA-MM-DD). Sem default de propósito: consultar o "
        "direito vigente à época de um fato é requisito, não caso de canto",
    )
    p.add_argument("--norma", action="append", default=None, help="URN da norma; repetível")
    p.add_argument(
        "--tipo",
        action="append",
        default=None,
        choices=[t.value for t in TipoDispositivo],
        help="restringe o tipo de dispositivo; repetível",
    )
    p.add_argument("--incluir-revogados", action="store_true", help="consulta histórica")
    p.add_argument("--k", type=int, default=None, help="trechos na saída (default: CJ_K_FINAL)")
    p.add_argument("--busca", type=int, default=None, help="candidatos da fusão RRF")
    p.add_argument(
        "--nivel",
        default=None,
        choices=[n.value for n in Nivel],
        help="até onde a expansão sobe (default: CJ_NIVEL_EXPANSAO)",
    )
    p.add_argument(
        "--sem-rerank",
        action="store_true",
        help="usa o reranker identidade: mostra a ordem crua da fusão",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=os.cpu_count(),
        help="threads do torch; o default toma a máquina toda (medido: rerank de 50 "
        "candidatos cai de 11,2s para 8,5s). A API não deve fazer isso — ela serve "
        "requisições concorrentes",
    )
    p.add_argument("--texto", action="store_true", help="imprime o trecho inteiro")
    p.add_argument("--json", action="store_true", help="saída para pipe, um objeto por linha")
    return p


def _imprimir(t: Trecho, i: int, *, inteiro: bool) -> None:
    fusao = f"{t.score_fusao:.5f}" if t.score_fusao is not None else "—"
    rerank = f"{t.score_rerank:+.4f}" if t.score_rerank is not None else "—"
    print(f"\n[{i:>2}] {t.rotulo_completo}")
    print(f"     fusão {fusao}   rerank {rerank}   {t.dispositivo_id}")
    texto = t.texto if inteiro else _resumir(t.texto)
    for linha in texto.splitlines():
        print(f"     {linha}")


def _resumir(texto: str, *, linhas: int = 3, largura: int = 100) -> str:
    corte = [ln[:largura] + ("…" if len(ln) > largura else "") for ln in texto.splitlines()]
    if len(corte) > linhas:
        corte = [*corte[:linhas], f"… (+{len(texto.splitlines()) - linhas} linhas)"]
    return "\n".join(corte)


def main(argv: list[str] | None = None) -> int:
    # Borda: o `huggingface_hub` fixa o `certifi` por dentro e só obedece a isto.
    confiar_no_sistema()
    args = construir_parser().parse_args(argv)
    cfg = obter_settings()

    from ..service import construir_recuperador

    t0 = time.perf_counter()
    rec = construir_recuperador(cfg, com_rerank=not args.sem_rerank, threads=args.threads)
    if args.nivel:
        rec.nivel = Nivel(args.nivel)
    carga = time.perf_counter() - t0

    criterios = Criterios(
        data_referencia=args.data,
        normas=tuple(args.norma or ()),
        tipos=tuple(TipoDispositivo(t) for t in (args.tipo or ())),
        incluir_revogados=args.incluir_revogados,
    )
    t1 = time.perf_counter()
    try:
        trechos = rec.recuperar(
            args.pergunta,
            criterios,
            k_busca=args.busca or cfg.k_busca,
            k_final=args.k or cfg.k_final,
        )
    finally:
        rec.conn.close()
    consulta_s = time.perf_counter() - t1

    if args.json:
        for t in trechos:
            print(json.dumps(t.model_dump(mode="json"), ensure_ascii=False))
        return 0

    print(
        f"{args.pergunta!r}   em {criterios.data_referencia}   "
        f"rerank={rec.reranker.nome}   expansão={rec.nivel.value}"
    )
    print(f"modelos em {carga:.1f}s, consulta em {consulta_s:.2f}s, {len(trechos)} trechos")
    for i, t in enumerate(trechos, 1):
        _imprimir(t, i, inteiro=args.texto)
    if not trechos:
        print("\nnenhum trecho: corpus vazio, filtro restritivo demais, ou coleção ausente")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
