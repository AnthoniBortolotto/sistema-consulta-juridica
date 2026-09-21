"""Métricas. Funções puras, sem I/O — testáveis sem corpus."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Citacao, Resposta


def recall_em_k(esperados: Sequence[str], recuperados: Sequence[str], k: int) -> float:
    """Fração dos dispositivos esperados presente nos k primeiros.

    Métrica principal da recuperação, e a que se mede sem chamar o modelo.
    """
    raise NotImplementedError


def mrr(esperados: Sequence[str], recuperados: Sequence[str]) -> float:
    """Recíproco da posição do primeiro acerto. Sensível à ordem, mede o reranker."""
    raise NotImplementedError


def acuracia_citacao(citacoes: Sequence[Citacao], esperados: Sequence[str]) -> float:
    """Fração das citações que apontam para um dispositivo esperado.

    Só é válida com backend que suporte citations nativas — ver `run.avaliar_ponta_a_ponta`.
    """
    raise NotImplementedError


def taxa_abstencao(respostas: Sequence[Resposta]) -> float:
    """Fração de respostas que se abstiveram."""
    raise NotImplementedError
