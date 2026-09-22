"""CLI da ingestão: `python -m consulta_juridica.ingest <estagio>`.

Estágios separados para poder repetir só o que falhou:

    python -m consulta_juridica.ingest baixar
    python -m consulta_juridica.ingest ingerir
    python -m consulta_juridica.ingest reindexar --norma urn:lex:...

É borda: junto com a API e o CLI do eval, um dos poucos lugares que leem `Settings` e
constroem dependências. O domínio recebe tudo pronto.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..config import obter_settings
from ..store import db
from ..tls import confiar_no_sistema
from . import fontes, pipeline
from .chunking import obter_estrategia


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m consulta_juridica.ingest",
        description="Ingestão: Planalto -> data/raw/ -> SQLite -> Qdrant.",
    )
    sub = p.add_subparsers(dest="estagio", required=True)

    b = sub.add_parser("baixar", help="estágio 1: fontes -> data/raw/ (depende de rede)")
    b.add_argument("--limite", type=int, default=None, help="baixar no máximo N normas")

    sub.add_parser("ingerir", help="estágio 2: data/raw/ -> SQLite (não toca no Qdrant)")

    r = sub.add_parser("reindexar", help="estágio 3: SQLite -> Qdrant (carrega os modelos)")
    r.add_argument("--norma", action="append", default=None, help="URN; repetível")
    r.add_argument(
        "--recriar",
        action="store_true",
        help="apaga e recria a coleção — necessário ao trocar de modelo ou de estratégia",
    )
    r.add_argument("--estrategia", default=None, help="sobrepõe CJ_ESTRATEGIA_CHUNK")
    r.add_argument(
        "--threads",
        type=int,
        default=os.cpu_count(),
        help="threads do torch; o default toma a máquina toda (medido: +19%%)",
    )

    sub.add_parser("status", help="o que já está no SQLite e no Qdrant")
    return p


def _baixar(args, cfg) -> int:
    caminhos = pipeline.baixar([fontes.FontePlanalto()], cfg.dir_raw, limite=args.limite)
    for c in caminhos:
        print(f"  {c.name}  {c.stat().st_size:,} bytes")
    print(f"{len(caminhos)} normas em {cfg.dir_raw}")
    return 0


def _ingerir(args, cfg) -> int:
    brutos = fontes.listar_brutos(cfg.dir_raw)
    if not brutos:
        print(f"nada com procedência em {cfg.dir_raw}: rode `baixar` primeiro", file=sys.stderr)
        return 1
    for o in fontes.orfaos(cfg.dir_raw):
        print(f"ignorado (sem .meta.json): {o.name}", file=sys.stderr)
    conn = db.conectar(cfg.caminho_sqlite)
    try:
        db.aplicar_schema(conn)
        rel = pipeline.ingerir(brutos, conn)
    finally:
        conn.close()
    print(f"{rel.normas} normas, {rel.dispositivos} dispositivos, {rel.remissoes} remissões")
    if rel.avisos:
        print(f"{len(rel.avisos)} avisos (primeiros 5):")
        for a in rel.avisos[:5]:
            print(f"  {a}")
    return 0


def _reindexar(args, cfg) -> int:
    from ..embedding import construir_encoder
    from ..vectorstore import cliente, garantir_colecao

    estrategia = obter_estrategia(args.estrategia or cfg.estrategia_chunk)
    print(f"estratégia: {estrategia.nome}   coleção: {cfg.colecao}")
    print(f"carregando {cfg.modelo_denso} e {cfg.modelo_esparso}…", flush=True)
    encoder = construir_encoder(cfg, threads=args.threads)
    print(f"  dim={encoder.dim}  idioma esparso={getattr(encoder, 'idioma', '?')}"
          f"  threads={args.threads}")

    client = cliente(cfg)
    garantir_colecao(
        client,
        nome=cfg.colecao,
        dim=encoder.dim,
        nome_modelo=encoder.nome_denso,
        recriar=args.recriar,
    )
    conn = db.conectar(cfg.caminho_sqlite, somente_leitura=True)
    try:
        rel = pipeline.reindexar(
            conn,
            client,
            encoder,
            estrategia,
            colecao=cfg.colecao,
            normas=args.norma,
        )
    finally:
        conn.close()
    print(
        f"{rel.normas} normas, {rel.chunks} chunks indexados, "
        f"{rel.removidos} removidos, {rel.duracao_s:.1f}s"
    )
    return 0


def _status(args, cfg) -> int:
    from ..store import queries
    from ..vectorstore import cliente

    brutos, orfaos = fontes.listar_brutos(cfg.dir_raw), fontes.orfaos(cfg.dir_raw)
    print(f"raw:    {len(brutos)} brutos com procedência em {cfg.dir_raw}"
          + (f"  ({len(orfaos)} sem .meta.json, ignorados)" if orfaos else ""))
    if Path(cfg.caminho_sqlite).exists():
        conn = db.conectar(cfg.caminho_sqlite, somente_leitura=True)
        try:
            normas = queries.normas_indexadas(conn)
            total = conn.execute("SELECT COUNT(*) FROM dispositivo").fetchone()[0]
            print(f"sqlite: {len(normas)} normas, {total} dispositivos")
            for urn, sha in normas:
                print(f"          {urn}  sha {sha[:12]}")
        finally:
            conn.close()
    else:
        print(f"sqlite: ausente ({cfg.caminho_sqlite})")

    try:
        from .indexer import contar

        client = cliente(cfg)
        if client.collection_exists(cfg.colecao):
            from ..vectorstore import metadados_colecao

            print(f"qdrant: {contar(client, colecao=cfg.colecao)} pontos em {cfg.colecao}")
            print(f"          {metadados_colecao(client, cfg.colecao)}")
        else:
            print(f"qdrant: coleção {cfg.colecao!r} não existe")
    except Exception as e:  # noqa: BLE001 - status nunca deve falhar por causa do Qdrant
        print(f"qdrant: indisponível ({type(e).__name__})")
    return 0


_ACOES = {"baixar": _baixar, "ingerir": _ingerir, "reindexar": _reindexar, "status": _status}


def main(argv: list[str] | None = None) -> int:
    # Borda: o `huggingface_hub` fixa o `certifi` por dentro e só obedece a isto.
    confiar_no_sistema()
    args = construir_parser().parse_args(argv)
    return _ACOES[args.estagio](args, obter_settings())


if __name__ == "__main__":
    raise SystemExit(main())
