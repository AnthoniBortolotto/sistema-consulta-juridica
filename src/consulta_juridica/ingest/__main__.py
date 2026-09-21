"""CLI da ingestão: `python -m consulta_juridica.ingest <estagio>`.

Estágios separados para poder repetir só o que falhou:

    python -m consulta_juridica.ingest baixar
    python -m consulta_juridica.ingest ingerir
    python -m consulta_juridica.ingest reindexar --norma urn:lex:...
"""

from __future__ import annotations

import argparse


def construir_parser() -> argparse.ArgumentParser:
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
