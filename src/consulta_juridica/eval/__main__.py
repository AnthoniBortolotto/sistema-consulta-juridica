"""CLI do eval: `python -m consulta_juridica.eval <modo>`.

    python -m consulta_juridica.eval recuperacao   # grátis, roda o tempo todo
    python -m consulta_juridica.eval e2e           # gasta tokens
"""

from __future__ import annotations

import argparse


def construir_parser() -> argparse.ArgumentParser:
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
