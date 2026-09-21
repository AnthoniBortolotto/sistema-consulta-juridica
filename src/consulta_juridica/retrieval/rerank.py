"""Reordenação por cross-encoder.

Maior ganho de qualidade por linha de código do pipeline: o bi-encoder compara vetores
pré-computados, o cross-encoder lê pergunta e trecho juntos.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .busca import Candidato


class Reranker(Protocol):
    nome: str

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]: ...


class RerankerCrossEncoder(Reranker):
    """`bge-reranker-v2-m3` local. Carregue uma vez e reutilize — são segundos de carga."""

    nome = "bge-reranker-v2-m3"

    def __init__(self, modelo: str) -> None:
        raise NotImplementedError


class RerankerIdentidade(Reranker):
    """No-op: preserva a ordem da fusão.

    Existe para o eval poder medir a recuperação pura e atribuir o ganho ao reranker.
    """

    nome = "identidade"

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]:
        return [0.0] * len(textos)


def rerankear(
    r: Reranker, consulta: str, cands: Sequence[Candidato], *, k: int = 8
) -> list[Candidato]:
    """Pontua e devolve os k melhores, com `score_rerank` preenchido."""
    raise NotImplementedError
