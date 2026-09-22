"""Filtro de vigência.

Prioridade máxima entre os testes: erro aqui não levanta exceção, apenas devolve resultado
errado. Norma revogada apresentada como vigente é o pior defeito possível no domínio.

Os testes rodam contra o Qdrant de verdade, e não contra o objeto `Filter` montado. Montar
o filtro é a parte fácil; o que quebra é a SEMÂNTICA do servidor — `range` sobre campo
ausente, ordinal contra inteiro, o índice de payload que não existe. Isso só o servidor
responde. Pulam quando ele não está de pé: `docker compose up -d`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from qdrant_client import models

from consulta_juridica.ingest.chunking import ChunkPorDispositivo
from consulta_juridica.ingest.indexer import indexar_norma
from consulta_juridica.models import TipoDispositivo
from consulta_juridica.retrieval.filtros import Criterios, construir_filtro
from consulta_juridica.store import queries
from consulta_juridica.store.queries import PREDICADO_VIGENTE
from consulta_juridica.vectorstore import (
    SENTINELA_VIGENTE,
    VETOR_DENSO,
    Campo,
    dia,
    id_ponto,
)

from .conftest import DIM, URN_CDC, colecao_de_sessao, esvaziar, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
INC2 = id_cdc("tit1", "cap3", "art6", "inc2")
INC4_REVOGADO = id_cdc("tit1", "cap3", "art6", "inc4")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")
INC10 = id_cdc("tit1", "cap3", "art6", "inc10")
INC11_VACATIO = id_cdc("tit1", "cap3", "art6", "inc11")

#: As duas bordas da árvore de teste, em `conftest.arvore_cdc`.
REVOGACAO = date(2020, 1, 1)
INICIO_VACATIO = date(2030, 1, 1)
UM_DIA = timedelta(days=1)


@pytest.fixture(scope="session")
def _colecao_pronta(client) -> Iterator[str]:
    yield from colecao_de_sessao(client, "teste_cj_filtros")


@pytest.fixture
def indexado(client, _colecao_pronta, corpus_cdc, encoder) -> str:
    """O art. 6º do CDC no índice, com o inciso revogado e o em vacatio legis."""
    colecao = esvaziar(client, _colecao_pronta)
    indexar_norma(client, corpus_cdc, encoder, ChunkPorDispositivo(), URN_CDC, colecao=colecao)
    return colecao


def ids_de(client, colecao: str, criterios: Criterios) -> set[str]:
    """Os dispositivos que sobrevivem ao filtro.

    `scroll` e não `query_points`: aqui o que se mede é o filtro, sem ranking nem fusão
    no meio para confundir uma exclusão com um resultado mal colocado.
    """
    pontos, _ = client.scroll(
        colecao, scroll_filter=construir_filtro(criterios), limit=1000, with_payload=True
    )
    return {p.payload[Campo.DISPOSITIVO_ID.value] for p in pontos}


def em(ref: date, **kwargs) -> Criterios:
    return Criterios(data_referencia=ref, **kwargs)


# --- os quatro casos ------------------------------------------------------------------


def test_dispositivo_vigente_aparece(indexado, client, data_ref):
    """Sem `revogado_em`, o dispositivo precisa passar pelo filtro.

    Caso da sentinela: `range(gt=ref)` no Qdrant exclui pontos sem o campo, então o não
    revogado sumiria se a sentinela não fosse gravada.
    """
    assert {ART6, INC2, INC8, INC10} <= ids_de(client, indexado, em(data_ref))


def test_dispositivo_revogado_antes_da_data_some(indexado, client, data_ref):
    assert INC4_REVOGADO not in ids_de(client, indexado, em(data_ref))


def test_dispositivo_revogado_depois_da_data_aparece(indexado, client):
    """Consulta sobre o direito vigente à época de um fato."""
    assert INC4_REVOGADO in ids_de(client, indexado, em(REVOGACAO - UM_DIA))


def test_dispositivo_ainda_nao_vigente_some(indexado, client, data_ref):
    """Vacatio legis: publicado, mas sem vigência na data de referência."""
    assert INC11_VACATIO not in ids_de(client, indexado, em(data_ref))
    assert INC11_VACATIO in ids_de(client, indexado, em(INICIO_VACATIO))


# --- a sentinela ----------------------------------------------------------------------


def test_sem_a_sentinela_o_vigente_desapareceria(indexado, client, data_ref):
    """Guarda de regressão da armadilha 6: `range(gt=)` exclui o ponto SEM o campo.

    Grava um ponto sem `revogado_em_dia` — que é o que aconteceria se alguém trocasse a
    sentinela por `None` — e confirma que ele some. É por isso que a sentinela existe, e o
    defeito não levantaria exceção nenhuma: o dispositivo em vigor simplesmente deixa de
    ser recuperável.
    """
    orfao = f"{URN_CDC}!art999"
    client.upsert(
        indexado,
        points=[
            models.PointStruct(
                id=str(id_ponto(orfao)),
                vector={VETOR_DENSO: [0.0] * DIM},
                payload={
                    Campo.DISPOSITIVO_ID.value: orfao,
                    Campo.NORMA_URN.value: URN_CDC,
                    Campo.VIGENCIA_INICIO.value: dia(date(1990, 9, 11)),
                    # Campo.REVOGADO_EM ausente de propósito.
                },
            )
        ],
        wait=True,
    )
    assert orfao not in ids_de(client, indexado, em(data_ref))


def test_sentinela_sobrevive_a_qualquer_data_de_referencia(indexado, client):
    """A sentinela é `date.max`, então nenhuma consulta plausível a ultrapassa."""
    assert dia(date(9999, 12, 31)) == SENTINELA_VIGENTE
    assert INC8 in ids_de(client, indexado, em(date(9999, 12, 30)))


# --- a borda da revogação -------------------------------------------------------------


def test_revogacao_produz_efeito_na_propria_data(indexado, client):
    """`gt` e não `gte`: no dia da revogação o dispositivo já não vale."""
    assert INC4_REVOGADO not in ids_de(client, indexado, em(REVOGACAO))
    assert INC4_REVOGADO in ids_de(client, indexado, em(REVOGACAO - UM_DIA))


@pytest.mark.parametrize("ref", [date(2019, 1, 1), REVOGACAO, date(2026, 1, 1), INICIO_VACATIO])
def test_qdrant_e_sqlite_concordam_sobre_o_que_vigora(indexado, client, corpus_cdc, ref):
    """O teste que justifica o módulo existir.

    O filtro do índice e o `PREDICADO_VIGENTE` do SQLite são duas escritas da mesma regra,
    em linguagens diferentes. Divergirem não levanta erro: a busca exclui o revogado e a
    expansão o traz de volta, ou o contrário. Aqui os dois respondem a mesma pergunta sobre
    o mesmo corpus, em quatro datas que cruzam as duas bordas.

    A comparação é restrita ao que virou chunk: dispositivo estrutural não entra no índice,
    e cobrá-lo do Qdrant seria comparar coisas diferentes.
    """
    norma = queries.obter_norma(corpus_cdc, URN_CDC)
    disps = list(queries.iter_para_indexar(corpus_cdc, norma_urn=URN_CDC))
    indexaveis = {c.payload.dispositivo_id for c in ChunkPorDispositivo().chunks(disps, norma)}
    do_sqlite = {
        r["id"]
        for r in corpus_cdc.execute(
            f"SELECT d.id FROM dispositivo d WHERE {PREDICADO_VIGENTE}",
            {"ref": ref.isoformat()},
        )
    }
    assert ids_de(client, indexado, em(ref)) == do_sqlite & indexaveis


# --- os demais recortes ---------------------------------------------------------------


def test_incluir_revogados_traz_o_revogado_mas_nao_o_em_vacatio(indexado, client, data_ref):
    """Consulta histórica solta a revogação, não o início de vigência.

    Quem pede o histórico quer o que já esteve em vigor até a data — texto que só entra em
    vigor em 2030 nunca foi direito aplicável a data nenhuma do passado.
    """
    ids = ids_de(client, indexado, em(data_ref, incluir_revogados=True))
    assert INC4_REVOGADO in ids
    assert INC11_VACATIO not in ids


def test_recorte_por_norma(indexado, client, data_ref):
    outra = "urn:lex:br:federal:lei:2002-01-10;10406"
    assert ids_de(client, indexado, em(data_ref, normas=(outra,))) == set()
    assert INC8 in ids_de(client, indexado, em(data_ref, normas=(URN_CDC,)))


def test_recorte_por_tipo(indexado, client, data_ref):
    assert ids_de(client, indexado, em(data_ref, tipos=(TipoDispositivo.ARTIGO,))) == {ART6}


def test_criterios_nao_tem_default_de_data():
    """Sem default é decisão de design, não esquecimento: um default viraria `date.today()`
    e a consulta retroativa morreria em silêncio."""
    with pytest.raises(TypeError):
        Criterios()  # type: ignore[call-arg]


def test_a_divergencia_conhecida_entre_os_dois_lados(indexado, client, corpus_cdc):
    """Antes da publicação da norma os dois lados discordam, e é decisão, não descuido.

    `PREDICADO_VIGENTE` trata `vigencia_inicio IS NULL` como "sempre vigeu"; o payload não
    pode fazer isso, porque `range(lte=ref)` exclui ponto sem o campo — então o chunking
    grava a data de publicação da norma no lugar do nulo. Resultado: para uma data anterior
    à publicação, o índice devolve zero e o SQLite devolve tudo.

    O índice é quem está certo (o CDC não vigorava em 1989), e a divergência só aparece
    fora da janela de existência da norma. Fica pinada aqui para virar decisão explícita
    em vez de surpresa na fase 6.
    """
    antes_da_publicacao = date(1989, 1, 1)
    do_sqlite = corpus_cdc.execute(
        f"SELECT COUNT(*) FROM dispositivo d WHERE {PREDICADO_VIGENTE}",
        {"ref": antes_da_publicacao.isoformat()},
    ).fetchone()[0]

    assert ids_de(client, indexado, em(antes_da_publicacao)) == set()
    assert do_sqlite > 0
