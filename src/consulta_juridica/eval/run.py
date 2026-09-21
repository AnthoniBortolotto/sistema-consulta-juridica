"""Execução do eval."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..retrieval.pipeline import Recuperador
from ..service import Servico
from .golden import ItemGolden


@dataclass
class RelatorioRecuperacao:
    """Resultado da avaliação de recuperação. Registra a configuração medida."""

    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    estrategia_chunk: str = ""
    reranker: str = ""
    n_itens: int = 0


@dataclass
class RelatorioE2E:
    """Resultado ponta a ponta.

    `backend` e `versao_prompt` entram no relatório porque duas rodadas só são comparáveis
    se ambos forem iguais.
    """

    acuracia_citacao: float = 0.0
    taxa_abstencao: float = 0.0
    abstencao_correta: float = 0.0
    backend: str = ""
    modelo: str = ""
    versao_prompt: str = ""
    custo_estimado_usd: float = 0.0
    n_itens: int = 0


def avaliar_recuperacao(
    rec: Recuperador, itens: Sequence[ItemGolden], ks: tuple[int, ...] = (5, 10, 20)
) -> RelatorioRecuperacao:
    """Mede a recuperação SEM chamar o modelo.

    É a função que vai rodar centenas de vezes: chunking, busca híbrida e reranker se
    ajustam inteiramente por aqui, de graça. Separada de propósito.
    """
    raise NotImplementedError


def avaliar_ponta_a_ponta(svc: Servico, itens: Sequence[ItemGolden]) -> RelatorioE2E:
    """Mede resposta e citação. Custa tokens.

    Deve recusar-se a rodar com um backend sem citations nativas: a acurácia de citação
    medida assim não significa nada, e um número inválido no README é pior que nenhum.
    """
    raise NotImplementedError
