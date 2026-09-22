"""Execução do eval."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..models import Trecho
from ..retrieval.filtros import Criterios
from ..retrieval.pipeline import Recuperador
from ..service import Servico
from ..urn import sem_versao
from .golden import ItemGolden, Mecanismo
from .metrics import mrr, recall_em_k


@dataclass
class ResultadoItem:
    """Uma pergunta medida. Existe para o relatório dizer QUAL pergunta falhou.

    Um recall agregado de 0,85 não diz o que consertar; `faltando` e `posicao` dizem.
    """

    id: str
    mecanismo: Mecanismo
    pergunta: str
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    posicao: int | None = None
    faltando: list[str] = field(default_factory=list)
    recuperados: list[str] = field(default_factory=list)


@dataclass
class RelatorioRecuperacao:
    """Resultado da avaliação de recuperação. Registra a configuração medida."""

    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    estrategia_chunk: str = ""
    reranker: str = ""
    n_itens: int = 0
    nivel_expansao: str = ""
    k_busca: int = 0
    n_abstencao: int = 0
    duracao_s: float = 0.0
    por_mecanismo: dict[str, RelatorioRecuperacao] = field(default_factory=dict)
    itens: list[ResultadoItem] = field(default_factory=list)


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
    rec: Recuperador,
    itens: Sequence[ItemGolden],
    ks: tuple[int, ...] = (5, 10, 20),
    *,
    k_busca: int = 50,
    estrategia_chunk: str = "",
) -> RelatorioRecuperacao:
    """Mede a recuperação SEM chamar o modelo.

    É a função que vai rodar centenas de vezes: chunking, busca híbrida e reranker se
    ajustam inteiramente por aqui, de graça. Separada de propósito.

    Duas decisões de método:

    - **`data_referencia` vem do item, nunca de fora.** `vig-01` e `vig-02` são a mesma
      pergunta em datas diferentes; um único `ref` para a rodada inteira apagaria metade do
      que o golden mede.
    - **Os itens de abstenção ficam fora da média.** Não há dispositivo para recuperar
      neles, e contá-los como acerto inflaria o recall com perguntas que o sistema nem
      deveria responder. Eles continuam no relatório por item, porque ver o que a busca
      trouxe para `abs-01` é o que revela se o sistema vai improvisar na fase 9.

    `estrategia_chunk` vem de fora porque a coleção do Qdrant **não** registra com qual
    estratégia foi construída — só modelo e dimensão. É o que a configuração diz, não o que
    o índice prova; ver a nota da fase 6 em NOTAS-DESIGN.
    """
    t0 = time.perf_counter()
    k_max = max(ks)
    resultados: list[ResultadoItem] = []

    for item in itens:
        trechos = rec.recuperar(
            item.pergunta,
            Criterios(data_referencia=item.data_referencia),
            k_busca=k_busca,
            k_final=k_max,
        )
        resultados.append(_medir(item, trechos, ks))

    medidos = [r for r, i in zip(resultados, itens, strict=True) if not i.deve_abster]
    relatorio = _agregar(medidos, ks)
    relatorio.estrategia_chunk = estrategia_chunk
    relatorio.reranker = rec.reranker.nome
    relatorio.nivel_expansao = rec.nivel.value
    relatorio.k_busca = k_busca
    relatorio.n_abstencao = len(itens) - len(medidos)
    relatorio.duracao_s = time.perf_counter() - t0
    relatorio.itens = resultados
    relatorio.por_mecanismo = {
        mec: _agregar([r for r in medidos if r.mecanismo == mec], ks)
        for mec in sorted({r.mecanismo for r in medidos})
    }
    return relatorio


def _medir(item: ItemGolden, trechos: Sequence[Trecho], ks: tuple[int, ...]) -> ResultadoItem:
    """Recall e MRR de uma pergunta, mais o que faltou — que é o que se depura."""
    ranking = [t.dispositivos or [t.dispositivo_id] for t in trechos]
    cobertos = {sem_versao(d) for trecho in ranking for d in trecho}
    faltando = [
        rotulo
        for rotulo, esperado in zip(
            item.rotulos_esperados, item.dispositivos_esperados, strict=True
        )
        if sem_versao(esperado) not in cobertos
    ]
    pontuacao = mrr(item.dispositivos_esperados, ranking)
    return ResultadoItem(
        id=item.id,
        mecanismo=item.mecanismo,
        pergunta=item.pergunta,
        recall={k: recall_em_k(item.dispositivos_esperados, ranking, k) for k in ks},
        mrr=pontuacao,
        posicao=round(1 / pontuacao) if pontuacao else None,
        faltando=faltando,
        recuperados=[t.rotulo_completo for t in trechos],
    )


def _agregar(rs: Sequence[ResultadoItem], ks: tuple[int, ...]) -> RelatorioRecuperacao:
    """Média simples por item. Cada pergunta pesa igual — o golden é pequeno e curado, e
    ponderar por número de esperados faria `amb-01`, com três, valer o triplo."""
    if not rs:
        return RelatorioRecuperacao(n_itens=0)
    return RelatorioRecuperacao(
        recall={k: sum(r.recall[k] for r in rs) / len(rs) for k in ks},
        mrr=sum(r.mrr for r in rs) / len(rs),
        n_itens=len(rs),
    )


def avaliar_ponta_a_ponta(svc: Servico, itens: Sequence[ItemGolden]) -> RelatorioE2E:
    """Mede resposta e citação. Custa tokens.

    Deve recusar-se a rodar com um backend sem citations nativas: a acurácia de citação
    medida assim não significa nada, e um número inválido no README é pior que nenhum.
    """
    raise NotImplementedError
