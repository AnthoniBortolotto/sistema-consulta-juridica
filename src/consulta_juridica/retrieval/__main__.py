"""Inspeção da recuperação sem gastar token.

    python -m consulta_juridica.retrieval "prazo para contestação" --data 2026-09-20

Imprime os trechos recuperados com os scores de fusão e de rerank. A maior parte da
iteração do projeto acontece aqui, não na geração: recall@k se mede sem chamar o modelo.
"""

from __future__ import annotations

import argparse


def construir_parser() -> argparse.ArgumentParser:
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
