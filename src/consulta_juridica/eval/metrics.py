"""Métricas. Funções puras, sem I/O — testáveis sem corpus.

**O ranking é uma lista de CONJUNTOS, não de IDs.** O esqueleto pedia
`Sequence[str]`, e isso não sobrevive à expansão: um trecho é um artigo inteiro e cobre
dezenas de dispositivos, então achatar tudo numa lista faria `k` contar dispositivos em vez
de trechos — recall@5 mediria os cinco primeiros incisos do primeiro artigo. Cada posição
do ranking é o conjunto de dispositivos que aquele trecho levou ao modelo.

A comparação ignora o sufixo de redação (`art6@3` casa com `art6`): o golden anota o
dispositivo, e qual redação responde é o que a data de referência decide.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Citacao, Resposta
from ..urn import sem_versao


def _normalizar(ids: Sequence[str]) -> set[str]:
    return {sem_versao(i) for i in ids}


def recall_em_k(esperados: Sequence[str], recuperados: Sequence[Sequence[str]], k: int) -> float:
    """Fração dos dispositivos esperados presente nos k primeiros.

    Métrica principal da recuperação, e a que se mede sem chamar o modelo.

    Item sem esperados (os de abstenção) devolve 1.0: não há nada para recuperar, e
    devolver 0.0 puxaria a média para baixo punindo o sistema por uma pergunta que ele nem
    deveria responder. Quem mede esses itens é a taxa de abstenção, na fase 9.
    """
    alvo = _normalizar(esperados)
    if not alvo:
        return 1.0
    cobertos = _normalizar([d for trecho in recuperados[:k] for d in trecho])
    return len(alvo & cobertos) / len(alvo)


def mrr(esperados: Sequence[str], recuperados: Sequence[Sequence[str]]) -> float:
    """Recíproco da posição do primeiro acerto. Sensível à ordem, mede o reranker.

    O recall diz se o dispositivo certo apareceu; só o MRR diz se ele apareceu em primeiro.
    É a diferença entre um sistema usável e um que obriga a ler oito trechos.
    """
    alvo = _normalizar(esperados)
    if not alvo:
        return 1.0
    for posicao, trecho in enumerate(recuperados, start=1):
        if alvo & _normalizar(trecho):
            return 1.0 / posicao
    return 0.0


def acuracia_citacao(citacoes: Sequence[Citacao], esperados: Sequence[str]) -> float:
    """Fração das citações que apontam para um dispositivo esperado.

    Só é válida com backend que suporte citations nativas — ver `run.avaliar_ponta_a_ponta`.
    """
    raise NotImplementedError


def taxa_abstencao(respostas: Sequence[Resposta]) -> float:
    """Fração de respostas que se abstiveram."""
    raise NotImplementedError
