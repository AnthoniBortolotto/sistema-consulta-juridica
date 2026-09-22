"""Busca híbrida, rerank e a orquestração do `Recuperador`.

Dois níveis, de propósito:

- **contrato da chamada ao Qdrant** — com um cliente dublê, porque a armadilha da busca
  híbrida (o filtro que não desce para os prefetch) não levanta erro nenhum: produz um
  ranking pior, que só um eval detectaria semanas depois;
- **ponta a ponta** — contra o Qdrant de verdade, com encoder dublê. Pula sem servidor.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from consulta_juridica.embedding import Encoder, Vetores
from consulta_juridica.ingest.chunking import ChunkPorDispositivo
from consulta_juridica.ingest.indexer import indexar_norma
from consulta_juridica.retrieval.busca import buscar, hidratar
from consulta_juridica.retrieval.expansao import Nivel
from consulta_juridica.retrieval.filtros import Criterios, construir_filtro
from consulta_juridica.retrieval.pipeline import FOLGA_DEDUP, Recuperador
from consulta_juridica.retrieval.rerank import (
    RerankerIdentidade,
    rerankear,
    texto_para_rerank,
)
from consulta_juridica.vectorstore import VETOR_DENSO, VETOR_ESPARSO

from .conftest import URN_CDC, EncoderFalso, candidatos_de, colecao_de_sessao, esvaziar, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
INC2 = id_cdc("tit1", "cap3", "art6", "inc2")
INC4_REVOGADO = id_cdc("tit1", "cap3", "art6", "inc4")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")
INC10 = id_cdc("tit1", "cap3", "art6", "inc10")

TEXTO_REVOGADO = "inciso hipotético revogado"


# --- contrato da chamada ao Qdrant ----------------------------------------------------


@dataclass
class ClienteEspiao:
    """Captura os argumentos de `query_points`. Não fala com servidor nenhum."""

    chamada: dict[str, Any] = field(default_factory=dict)

    def query_points(self, colecao, **kwargs):
        self.chamada = {"colecao": colecao, **kwargs}
        return type("Resposta", (), {"points": []})()


@dataclass
class EncoderEspiao(Encoder):
    """Registra por qual caminho o texto passou.

    O BM25 do fastembed NÃO aplica IDF, e quem aplica é o Qdrant. Codificar a pergunta com
    `embed()` em vez de `query_embed()` pontuaria o termo pela frequência dentro da própria
    pergunta — recall silenciosamente pior, zero exceções. Este espião é o que impede a
    troca de passar despercebida.
    """

    nome_denso = "espiao"
    nome_esparso = "espiao"
    dim = 8
    caminhos: list[str] = field(default_factory=list)

    def documentos(self, textos: Sequence[str], *, lote: int = 32) -> Iterator[Vetores]:
        self.caminhos.append("documentos")
        yield from (EncoderFalso()._vetor(t) for t in textos)

    def consulta(self, texto: str) -> Vetores:
        self.caminhos.append("consulta")
        return EncoderFalso()._vetor(texto)


@pytest.fixture
def espioes(data_ref):
    client, encoder = ClienteEspiao(), EncoderEspiao()
    buscar(
        client,
        encoder,
        "prazo para contestação",
        Criterios(data_referencia=data_ref),
        colecao="qualquer",
        k=7,
        prefetch=99,
    )
    return client, encoder


def test_filtro_desce_para_os_dois_prefetch(espioes, data_ref):
    """A armadilha da busca híbrida.

    `query_filter` NÃO desce para os prefetch. Sem o filtro repetido dentro de cada um,
    cada perna gasta seus 150 lugares com dispositivos revogados e a lista fundida já
    chega empobrecida — a vigência teria sido aplicada depois da fusão.
    """
    client, _ = espioes
    esperado = construir_filtro(Criterios(data_referencia=data_ref))
    assert [p.filter for p in client.chamada["prefetch"]] == [esperado, esperado]
    assert client.chamada["query_filter"] == esperado


def test_busca_funde_as_duas_pernas_no_servidor(espioes):
    """RRF server-side: reimplementar a fusão no cliente criaria uma segunda fonte de
    verdade para o ranking."""
    client, _ = espioes
    assert [p.using for p in client.chamada["prefetch"]] == [VETOR_DENSO, VETOR_ESPARSO]
    assert client.chamada["query"].fusion == "rrf"


def test_prefetch_pesca_mais_fundo_que_a_saida(espioes):
    """A fusão só tem o que fundir se cada perna trouxer bem mais que o k final."""
    client, _ = espioes
    assert [p.limit for p in client.chamada["prefetch"]] == [99, 99]
    assert client.chamada["limit"] == 7


def test_pergunta_passa_pelo_caminho_de_consulta_do_encoder(espioes):
    _, encoder = espioes
    assert encoder.caminhos == ["consulta"]


# --- hidratação -----------------------------------------------------------------------


def test_hidratar_traz_o_texto_do_sqlite(corpus_cdc, arvore_cdc, norma_cdc):
    """O payload não carrega o texto de propósito: o SQLite é a fonte da verdade e
    duplicá-lo criaria duas cópias para divergir na próxima reingestão."""
    cands = candidatos_de(arvore_cdc, norma_cdc, [INC8])
    for c in cands:
        c.texto = ""
    (hidratado,) = hidratar(corpus_cdc, cands)
    assert hidratado.texto.startswith("a facilitação da defesa")


def test_hidratar_descarta_o_que_sumiu_do_corpus(corpus_cdc, arvore_cdc, norma_cdc):
    """Índice mais velho que o SQLite. Devolver o candidato sem texto seria pior: chegaria
    ao modelo como citação vazia."""
    cands = candidatos_de(arvore_cdc, norma_cdc, [INC8, INC2])
    cands[0].dispositivo_id = f"{URN_CDC}!art999"
    assert [c.dispositivo_id for c in hidratar(corpus_cdc, cands)] == [INC2]


# --- rerank ---------------------------------------------------------------------------


class RerankerPorTamanho(RerankerIdentidade):
    """Dublê determinístico: pontua pelo comprimento do texto. Não carrega modelo nenhum."""

    nome = "por-tamanho"

    def pontuar(self, consulta: str, textos: Sequence[str]) -> list[float]:
        return [float(len(t)) for t in textos]


def test_rerankear_reordena_e_preenche_o_score(arvore_cdc, norma_cdc):
    cands = candidatos_de(arvore_cdc, norma_cdc, [INC2, INC8], scores=[0.9, 0.1])
    melhores = rerankear(RerankerPorTamanho(), "consumidor", cands, k=2)
    # O VIII é bem mais longo que o II: o rerank desfaz a ordem da fusão.
    assert [c.dispositivo_id for c in melhores] == [INC8, INC2]
    assert all(c.score_rerank is not None for c in melhores)
    assert [c.score for c in melhores] == [0.1, 0.9], "o score da fusão não é sobrescrito"


def test_rerankear_corta_em_k(arvore_cdc, norma_cdc):
    cands = candidatos_de(arvore_cdc, norma_cdc, [INC2, INC8, INC10])
    assert len(rerankear(RerankerPorTamanho(), "x", cands, k=2)) == 2


def test_identidade_preserva_a_ordem_da_fusao(arvore_cdc, norma_cdc):
    """A ordenação precisa ser estável: a identidade dá 0.0 a todo mundo, e é a
    estabilidade que faz o eval "sem rerank" medir a fusão, e não uma permutação."""
    ids = [INC10, INC2, INC8, ART6]
    cands = candidatos_de(arvore_cdc, norma_cdc, ids)
    melhores = rerankear(RerankerIdentidade(), "x", cands, k=4)
    assert [c.dispositivo_id for c in melhores] == ids


def test_rerankear_aceita_lista_vazia(arvore_cdc, norma_cdc):
    """Filtro restritivo demais devolve zero candidatos, e o cross-encoder explode com
    entrada vazia."""
    assert rerankear(RerankerPorTamanho(), "x", [], k=8) == []


def test_o_cross_encoder_le_o_rotulo_junto_com_o_texto(arvore_cdc, norma_cdc):
    """Metade do golden set pergunta por numeração ("art. 5º, LXXVIII"); sem o rótulo o
    cross-encoder não tem como distinguir o dispositivo certo de um vizinho parecido."""
    (c,) = candidatos_de(arvore_cdc, norma_cdc, [INC8])
    texto = texto_para_rerank(c)
    assert texto.startswith("Lei 8.078/1990, Art. 6º, VIII")
    assert c.texto in texto


# --- ponta a ponta --------------------------------------------------------------------


@pytest.fixture(scope="session")
def _colecao_pronta(client) -> Iterator[str]:
    yield from colecao_de_sessao(client, "teste_cj_recuperacao")


@pytest.fixture
def recuperador(client, _colecao_pronta, corpus_cdc, encoder) -> Recuperador:
    colecao = esvaziar(client, _colecao_pronta)
    indexar_norma(client, corpus_cdc, encoder, ChunkPorDispositivo(), URN_CDC, colecao=colecao)
    return Recuperador(
        conn=corpus_cdc,
        client=client,
        encoder=encoder,
        reranker=RerankerIdentidade(),
        colecao=colecao,
    )


def test_recuperar_devolve_trecho_pronto_para_o_modelo(recuperador, data_ref):
    trechos = recuperador.recuperar("direitos básicos", Criterios(data_referencia=data_ref))
    assert trechos
    t = trechos[0]
    assert t.rotulo_completo and t.fonte_url and t.texto
    assert t.score_fusao is not None


def test_a_expansao_funde_os_incisos_num_artigo_so(recuperador, data_ref):
    """Os seis chunks do art. 6º expandem para o mesmo artigo: o modelo recebe um trecho,
    não seis cópias do mesmo texto."""
    trechos = recuperador.recuperar("direitos básicos", Criterios(data_referencia=data_ref))
    assert [t.dispositivo_id for t in trechos] == [ART6]


def test_o_inciso_revogado_nao_chega_ao_modelo(recuperador, data_ref):
    """O caminho completo do vazamento: o filtro do Qdrant exclui o inciso revogado, e a
    expansão poderia trazê-lo de volta pelo SQLite."""
    (trecho,) = recuperador.recuperar("direitos", Criterios(data_referencia=data_ref))
    assert TEXTO_REVOGADO not in trecho.texto


def test_consulta_retroativa_traz_a_redacao_da_epoca(recuperador):
    (trecho,) = recuperador.recuperar("direitos", Criterios(data_referencia=date(2019, 1, 1)))
    assert TEXTO_REVOGADO in trecho.texto


def test_nivel_nenhum_devolve_os_dispositivos_crus(recuperador, data_ref):
    """Sem expansão, cada chunk vira um trecho — é assim que o eval isola o ganho do
    parent-document retrieval."""
    recuperador.nivel = Nivel.NENHUM
    trechos = recuperador.recuperar(
        "direitos", Criterios(data_referencia=data_ref), k_final=8
    )
    assert {t.dispositivo_id for t in trechos} == {ART6, INC2, INC8, INC10}


def test_k_final_limita_a_saida(recuperador, data_ref):
    recuperador.nivel = Nivel.NENHUM
    trechos = recuperador.recuperar(
        "direitos", Criterios(data_referencia=data_ref), k_final=2
    )
    assert len(trechos) == 2


def test_corpus_sem_resposta_devolve_vazio(recuperador, data_ref):
    """Filtro que não casa com nada. A abstenção por falta de candidato é do serviço, mas
    ela depende de a recuperação devolver lista vazia em vez de explodir."""
    criterios = Criterios(data_referencia=data_ref, normas=("urn:lex:br:federal:lei:0;0",))
    assert recuperador.recuperar("qualquer coisa", criterios) == []


def test_folga_de_dedup_e_maior_que_um():
    """Se a folga fosse 1, o `k_final` viraria teto antes da deduplicação e o modelo
    receberia menos trechos do que foi pedido."""
    assert FOLGA_DEDUP > 1


@dataclass
class ClienteComEmpate:
    """Devolve dois pontos com a MESMA pontuação de fusão, na ordem que o servidor quiser."""

    pontos: list

    def query_points(self, colecao, **kwargs):
        return type("Resposta", (), {"points": self.pontos})()


def test_empate_de_fusao_tem_ordem_estavel(arvore_cdc, norma_cdc, data_ref):
    """O RRF soma recíprocos de posição, então empate exato é comum — no golden, o art. 37
    da CF e o art. 43 do CC saem os dois com 0,83333. Deixar o desempate para o servidor
    fazia o MRR do eval oscilar entre 0,758 e 0,848 na MESMA configuração, o que impede
    atribuir qualquer diferença a uma mudança de código."""
    from qdrant_client.models import ScoredPoint

    cands = candidatos_de(arvore_cdc, norma_cdc, [INC8, INC2])
    pontos = [
        ScoredPoint(
            id=str(c.chunk_id), version=0, score=0.5, payload=c.payload.model_dump(mode="json")
        )
        for c in cands
    ]
    ordens = [
        [
            c.dispositivo_id
            for c in buscar(
                ClienteComEmpate(p),
                EncoderFalso(),
                "x",
                Criterios(data_referencia=data_ref),
                colecao="qualquer",
            )
        ]
        for p in (pontos, list(reversed(pontos)))
    ]
    assert ordens[0] == ordens[1], "a ordem do servidor não pode vazar para o resultado"
    assert ordens[0] == sorted(ordens[0])
