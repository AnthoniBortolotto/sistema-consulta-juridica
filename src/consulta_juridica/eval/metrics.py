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
from ..urn import SEPARADOR_SEGMENTO, sem_versao


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


def cita_dentro(citado: str, esperado: str) -> bool:
    """A citação cai no dispositivo esperado ou DENTRO dele?

    O golden anota "CDC art. 49", e a citação pode vir no parágrafo único do art. 49 —
    está correta: é texto do dispositivo esperado, citado com mais precisão do que se
    anotou. O inverso não vale: esperar o inciso VIII e receber o caput do art. 6º é citar
    a moldura, não o que sustenta a afirmação.

    Descendente se reconhece pelo ID: o fragmento do filho é o do pai seguido de `_`.
    O separador é o que impede `art1` de casar com `art10`.
    """
    c, e = sem_versao(citado), sem_versao(esperado)
    return c == e or c.startswith(e + SEPARADOR_SEGMENTO)


def acuracia_citacao(citacoes: Sequence[Citacao], esperados: Sequence[str]) -> float:
    """Fração das citações que apontam para um dispositivo esperado.

    Só é válida com backend que suporte citations nativas — ver `run.avaliar_ponta_a_ponta`.

    Resposta sem citação nenhuma vale 0.0, e não "não se aplica": uma afirmação jurídica
    sem citação é exatamente o que este sistema existe para não produzir. (Quem não deve
    entrar na conta é a resposta que se ABSTEVE — essa é filtrada por quem chama, e medida
    pela taxa de abstenção.)

    Precisão contra a anotação, e com o limite que isso tem: uma citação correta a um
    dispositivo que o golden não anotou conta como erro. Com 13 perguntas curadas isso é
    raro; num golden maior, vira o ruído dominante da métrica.
    """
    if not citacoes:
        return 0.0
    corretas = sum(
        any(cita_dentro(c.dispositivo_id, e) for e in esperados) for c in citacoes
    )
    return corretas / len(citacoes)


def taxa_abstencao(respostas: Sequence[Resposta]) -> float:
    """Fração de respostas que se abstiveram.

    Sozinha, é uma métrica que se ganha trapaceando: um sistema que sempre se abstém tem
    taxa 1.0 nas perguntas que pedem abstenção. Por isso o relatório a quebra em duas —
    abstenção correta (entre as que deviam) e indevida (entre as que não deviam) — e é o
    par que diz alguma coisa.
    """
    if not respostas:
        return 0.0
    return sum(r.abstencao is not None for r in respostas) / len(respostas)
