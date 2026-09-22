"""Execução do eval."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from ..generation.backend import Pedido
from ..generation.cache import BackendComCache
from ..generation.prompt import VERSAO_PROMPT
from ..models import Trecho, Uso
from ..retrieval.filtros import Criterios
from ..retrieval.pipeline import Recuperador
from ..service import Consulta, Servico
from ..urn import sem_versao
from .golden import ItemGolden, Mecanismo
from .metrics import acuracia_citacao, cita_dentro, mrr, recall_em_k


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


@dataclass
class ResultadoE2E:
    """Uma pergunta respondida. Com 13 perguntas, é o relatório por item que se lê — o
    agregado de uma amostra desse tamanho diz menos do que parece."""

    id: str
    mecanismo: Mecanismo
    deve_abster: bool
    abstencao: str | None
    #: `None` quando a métrica não se aplica: a pergunta pedia abstenção, ou o sistema se
    #: absteve. Zero é outra coisa — respondeu e citou errado, ou não citou.
    acuracia: float | None
    citados: list[str] = field(default_factory=list)
    corretas: int = 0
    texto: str = ""
    pago: bool = False
    custo_usd: float | None = None


@dataclass
class RelatorioE2E:
    """Resultado ponta a ponta.

    `backend` e `versao_prompt` entram no relatório porque duas rodadas só são comparáveis
    se ambos forem iguais.

    As três taxas de abstenção existem juntas porque cada uma sozinha se ganha trapaceando:
    um sistema que sempre se abstém tem `abstencao_correta` 1.0, e um que nunca se abstém
    tem `abstencao_indevida` 0.0. É o par correta/indevida que diz se ele está calibrado.
    """

    acuracia_citacao: float = 0.0
    taxa_abstencao: float = 0.0
    abstencao_correta: float = 0.0
    abstencao_indevida: float = 0.0
    sem_citacao: int = 0
    backend: str = ""
    modelo: str = ""
    versao_prompt: str = ""
    #: Só das chamadas que ESTA rodada pagou. Resposta lida do cache carrega o `uso` da
    #: chamada original, e somá-lo contaria duas vezes o mesmo gasto.
    custo_estimado_usd: float | None = 0.0
    chamadas_pagas: int = 0
    n_itens: int = 0
    duracao_s: float = 0.0
    itens: list[ResultadoE2E] = field(default_factory=list)


@dataclass
class Estimativa:
    """O que uma rodada ponta a ponta custaria, calculado sem chamar o modelo."""

    n_itens: int
    do_cache: int
    sem_candidatos: int
    a_pagar: int
    tokens_entrada: int
    custo_min_usd: float | None
    custo_max_usd: float | None
    modelo: str


#: US$ por milhão de tokens (entrada, saída), da tabela da documentação em 2026-06-24.
#: Estimativa, não fatura: a escrita de cache custa 1,25x e não está em `Uso`, então o
#: número fica ligeiramente abaixo do real na primeira rodada.
PRECO_USD_POR_MTOK: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

#: Leitura de cache custa cerca de um décimo da entrada normal.
FATOR_LEITURA_CACHE: Final = 0.1

#: Conservador de propósito para texto jurídico em português: errar para mais no custo
#: estimado é o erro seguro. A primeira rodada paga dá o número real em `Uso`.
CARACTERES_POR_TOKEN: Final = 3.0

#: Piso da saída na estimativa. O teto é o `max_tokens` do pedido — com thinking
#: adaptativo, o raciocínio conta para ele, e não há como saber antes quanto será usado.
SAIDA_MINIMA_TOKENS: Final = 500


def _preco(modelo: str) -> tuple[float, float] | None:
    """Preço pelo prefixo mais longo: o ID devolvido pela API pode vir com sufixo."""
    for nome in sorted(PRECO_USD_POR_MTOK, key=len, reverse=True):
        if modelo.startswith(nome):
            return PRECO_USD_POR_MTOK[nome]
    return None


def custo_de(uso: Uso, modelo: str) -> float | None:
    """Custo em US$ de uma chamada. `None` para modelo fora da tabela — nunca um chute."""
    preco = _preco(modelo)
    if preco is None:
        return None
    entrada, saida = preco
    return (
        uso.tokens_entrada * entrada
        + uso.tokens_cache_leitura * entrada * FATOR_LEITURA_CACHE
        + uso.tokens_saida * saida
    ) / 1_000_000


def _cache_de(svc: Servico) -> BackendComCache | None:
    return svc.backend if isinstance(svc.backend, BackendComCache) else None


def _exigir_citacoes(svc: Servico) -> None:
    if not svc.backend.suporta_citacoes:
        raise ValueError(
            f"o backend {svc.backend.nome!r} não tem citations nativas: as citações dele são "
            "âncoras que o modelo pode ou não emitir, e a acurácia medida assim não significa "
            "nada. Use CJ_BACKEND_LLM=api."
        )


def estimar_ponta_a_ponta(svc: Servico, itens: Sequence[ItemGolden]) -> Estimativa:
    """Quanto a rodada custaria, SEM chamar o modelo.

    Monta cada pedido exatamente como seria enviado e consulta o cache pela chave dele: o
    que já foi respondido é grátis, o que ficou sem trecho também. O resto é estimado por
    contagem de caracteres. A recuperação roda de verdade — é o único jeito de saber qual
    pedido seria enviado.
    """
    _exigir_citacoes(svc)
    cache = _cache_de(svc)
    do_cache = sem_candidatos = 0
    pedidos: list[Pedido] = []
    for item in itens:
        _, pedido = svc.montar(_consulta(item))
        if pedido is None:
            sem_candidatos += 1
        elif cache is not None and cache.em_cache(pedido):
            do_cache += 1
        else:
            pedidos.append(pedido)

    tokens = sum(_tokens_estimados(p) for p in pedidos)
    preco = _preco(svc.modelo)
    if preco is None:
        custo_min = custo_max = None
    else:
        entrada, saida = preco
        base = tokens * entrada / 1_000_000
        custo_min = base + len(pedidos) * SAIDA_MINIMA_TOKENS * saida / 1_000_000
        custo_max = base + sum(p.max_tokens for p in pedidos) * saida / 1_000_000
    return Estimativa(
        n_itens=len(itens),
        do_cache=do_cache,
        sem_candidatos=sem_candidatos,
        a_pagar=len(pedidos),
        tokens_entrada=tokens,
        custo_min_usd=custo_min,
        custo_max_usd=custo_max,
        modelo=svc.modelo,
    )


def _tokens_estimados(p: Pedido) -> int:
    caracteres = len(p.sistema) + len(p.pergunta) + sum(
        len(d.texto) + len(d.titulo) + len(d.contexto) for d in p.documentos
    )
    return int(caracteres / CARACTERES_POR_TOKEN)


def _consulta(item: ItemGolden) -> Consulta:
    """A data vem do item, como no eval de recuperação: `vig-01` e `vig-02` são a mesma
    pergunta em datas diferentes."""
    return Consulta(pergunta=item.pergunta, data_referencia=item.data_referencia)


def avaliar_ponta_a_ponta(svc: Servico, itens: Sequence[ItemGolden]) -> RelatorioE2E:
    """Mede resposta e citação. Custa tokens.

    Deve recusar-se a rodar com um backend sem citations nativas: a acurácia de citação
    medida assim não significa nada, e um número inválido no README é pior que nenhum.

    Duas decisões de método:

    - **A acurácia de citação só conta as perguntas que o sistema respondeu e devia
      responder.** A que se absteve indevidamente já é falha, e medida pela
      `abstencao_indevida`; somá-la como 0 na acurácia contaria a mesma falha duas vezes,
      e o número deixaria de dizer se as citações que o sistema FAZ estão certas.
    - **`top_k` é o de produção (8), não o do eval de recuperação (20).** Aqui se mede o
      que o usuário recebe, e o modelo lê oito trechos, não vinte.
    """
    _exigir_citacoes(svc)
    t0 = time.perf_counter()
    cache = _cache_de(svc)
    resultados: list[ResultadoE2E] = []
    modelos: set[str] = set()

    for item in itens:
        faltas_antes = cache.faltas if cache is not None else 0
        resposta = svc.responder(_consulta(item))
        chamou = bool(resposta.modelo)
        pago = chamou and (cache is None or cache.faltas > faltas_antes)
        if chamou:
            modelos.add(resposta.modelo)

        acuracia = None
        corretas = 0
        if not item.deve_abster and resposta.abstencao is None:
            acuracia = acuracia_citacao(resposta.citacoes, item.dispositivos_esperados)
            corretas = sum(
                any(cita_dentro(c.dispositivo_id, e) for e in item.dispositivos_esperados)
                for c in resposta.citacoes
            )

        resultados.append(
            ResultadoE2E(
                id=item.id,
                mecanismo=item.mecanismo,
                deve_abster=item.deve_abster,
                abstencao=resposta.abstencao.value if resposta.abstencao else None,
                acuracia=acuracia,
                citados=[c.rotulo_completo for c in resposta.citacoes],
                corretas=corretas,
                texto=resposta.texto,
                pago=pago,
                custo_usd=custo_de(resposta.uso, resposta.modelo) if pago else 0.0,
            )
        )

    return _agregar_e2e(resultados, svc, modelos, time.perf_counter() - t0)


def _agregar_e2e(
    rs: Sequence[ResultadoE2E], svc: Servico, modelos: set[str], duracao: float
) -> RelatorioE2E:
    deviam_responder = [r for r in rs if not r.deve_abster]
    deviam_abster = [r for r in rs if r.deve_abster]
    respondidas = [r for r in deviam_responder if r.acuracia is not None]
    custos = [r.custo_usd for r in rs if r.pago]

    def fracao(xs, cond) -> float:
        return sum(1 for x in xs if cond(x)) / len(xs) if xs else 0.0

    return RelatorioE2E(
        acuracia_citacao=(
            sum(r.acuracia for r in respondidas) / len(respondidas) if respondidas else 0.0
        ),
        taxa_abstencao=fracao(rs, lambda r: r.abstencao is not None),
        abstencao_correta=fracao(deviam_abster, lambda r: r.abstencao is not None),
        abstencao_indevida=fracao(deviam_responder, lambda r: r.abstencao is not None),
        sem_citacao=sum(1 for r in respondidas if not r.citados),
        backend=svc.backend.nome,
        # O que a API DISSE que respondeu, não o que foi configurado. Dois modelos numa
        # rodada só (cache de uma configuração anterior) aparecem os dois.
        modelo=", ".join(sorted(modelos)) or svc.modelo,
        versao_prompt=VERSAO_PROMPT,
        custo_estimado_usd=None if any(c is None for c in custos) else sum(custos),
        chamadas_pagas=len(custos),
        n_itens=len(rs),
        duracao_s=duracao,
        itens=list(rs),
    )
