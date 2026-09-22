"""Coleção Qdrant, indexação e os três estágios da ingestão.

Estes testes NÃO carregam o bge-m3. O encoder é dublê: o que precisa ser protegido aqui é
o contrato — drift de modelo, apagar-antes-de-inserir, nomes de campo do payload e a
propriedade de que o índice é derivado do SQLite. A qualidade dos vetores é problema do
eval da fase 6, não de teste unitário.

Os testes que falam com o Qdrant pulam quando ele não está de pé: `docker compose up -d`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from qdrant_client.models import SparseVector

from consulta_juridica.embedding import Encoder, Vetores
from consulta_juridica.errors import ColecaoIncompativel, NormaNaoEncontrada
from consulta_juridica.ingest import pipeline
from consulta_juridica.ingest.chunking import ChunkPorDispositivo
from consulta_juridica.ingest.indexer import apagar_norma, contar, indexar_norma
from consulta_juridica.store import db
from consulta_juridica.vectorstore import (
    META_DIM,
    META_MODELO,
    SENTINELA_VIGENTE,
    VETOR_DENSO,
    VETOR_ESPARSO,
    Campo,
    garantir_colecao,
    metadados_colecao,
)

from .conftest import URN_CDC

DIM = 8


class EncoderFalso(Encoder):
    """Vetores determinísticos e baratos.

    Determinístico, não aleatório: um teste que falha tem de falhar sempre. O vetor é
    derivado do hash do texto, então textos diferentes dão vetores diferentes — é só
    disso que a indexação precisa.
    """

    nome_denso = "falso/denso"
    nome_esparso = "falso/esparso"
    dim = DIM

    def _vetor(self, texto: str) -> Vetores:
        h = abs(hash(texto))
        denso = [((h >> (i * 3)) % 97) / 97 for i in range(DIM)]
        return Vetores(denso=denso, esparso=SparseVector(indices=[h % 1000], values=[1.0]))

    def documentos(self, textos: Sequence[str], *, lote: int = 32) -> Iterator[Vetores]:
        for t in textos:
            yield self._vetor(t)

    def consulta(self, texto: str) -> Vetores:
        return self._vetor(texto)


@pytest.fixture(scope="session")
def client():
    from consulta_juridica.config import Settings
    from consulta_juridica.vectorstore import cliente

    c = cliente(Settings())
    try:
        c.get_collections()
    except Exception:  # noqa: BLE001
        pytest.skip("Qdrant fora do ar — `docker compose up -d`")
    return c


@pytest.fixture
def colecao(client) -> Iterator[str]:
    """Coleção descartável, para os testes que exercitam a CRIAÇÃO da coleção."""
    nome = "teste_cj_efemera"
    if client.collection_exists(nome):
        client.delete_collection(nome)
    yield nome
    if client.collection_exists(nome):
        client.delete_collection(nome)


@pytest.fixture(scope="session")
def _colecao_pronta(client) -> Iterator[str]:
    """Uma coleção para a sessão inteira, com os índices de payload já construídos.

    Medido: construir os 6 índices de payload custa ~21 s, e o `wait=False` só adia esse
    custo para o primeiro `upsert` síncrono. Pagar uma vez por sessão em vez de uma vez
    por teste tirou o suite de 4 min para segundos.
    """
    from qdrant_client import models

    nome = "teste_cj_indexacao"
    if client.collection_exists(nome):
        client.delete_collection(nome)
    garantir_colecao(client, nome=nome, dim=DIM, nome_modelo=EncoderFalso.nome_denso)
    # Um upsert síncrono força a construção dos índices agora, fora do tempo dos testes.
    client.upsert(
        nome,
        points=[models.PointStruct(id=1, vector={VETOR_DENSO: [0.0] * DIM}, payload={})],
        wait=True,
    )
    client.delete(nome, points_selector=models.PointIdsList(points=[1]), wait=True)
    yield nome
    client.delete_collection(nome)


@pytest.fixture
def colecao_limpa(client, _colecao_pronta) -> str:
    """A coleção da sessão, sem pontos."""
    from qdrant_client import models

    client.delete(
        _colecao_pronta, points_selector=models.FilterSelector(filter=models.Filter()), wait=True
    )
    return _colecao_pronta


@pytest.fixture
def encoder() -> Encoder:
    return EncoderFalso()


# --- coleção --------------------------------------------------------------------------


def test_garantir_colecao_cria_as_duas_pernas(client, colecao):
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    info = client.get_collection(colecao)
    assert info.config.params.vectors[VETOR_DENSO].size == DIM
    assert info.config.params.sparse_vectors[VETOR_ESPARSO].modifier == "idf"


def test_modifier_idf_e_obrigatorio(client, colecao):
    """O BM25 do fastembed entrega frequência crua; sem IDF no servidor, termo comum
    pesa igual a termo raro e a perna esparsa vira contagem de palavra."""
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    assert client.get_collection(colecao).config.params.sparse_vectors[VETOR_ESPARSO].modifier


def test_garantir_colecao_e_idempotente(client, colecao):
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    assert metadados_colecao(client, colecao) == {META_MODELO: "falso/denso", META_DIM: str(DIM)}


def test_drift_de_modelo_levanta(client, colecao):
    """Seguir adiante com outro modelo degrada a recuperação sem sintoma visível."""
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    with pytest.raises(ColecaoIncompativel, match="falso/denso"):
        garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="outro/modelo")


def test_drift_de_dimensao_levanta(client, colecao):
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    with pytest.raises(ColecaoIncompativel):
        garantir_colecao(client, nome=colecao, dim=DIM + 1, nome_modelo="falso/denso")


def test_recriar_aceita_o_novo_modelo(client, colecao):
    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    garantir_colecao(client, nome=colecao, dim=16, nome_modelo="outro/modelo", recriar=True)
    assert metadados_colecao(client, colecao) == {META_MODELO: "outro/modelo", META_DIM: "16"}


def _esperar_schema(client, colecao, campos: set[str], *, limite_s: float = 20.0) -> set[str]:
    """Os índices são criados com `wait=False`; o schema aparece logo depois."""
    import time

    fim = time.monotonic() + limite_s
    visto: set[str] = set()
    while time.monotonic() < fim:
        visto = set(client.get_collection(colecao).payload_schema or {})
        if campos <= visto:
            return visto
        time.sleep(0.2)
    return visto


def test_indices_de_payload(client, colecao):
    """Os índices são criados, e só os que faltam.

    Sem índice, o filtro de vigência degrada para varredura. E recriar não é no-op no
    Qdrant: medi 3,5 s por campo, que seriam pagos a cada reindexação.

    As duas asserções vivem no mesmo teste de propósito: construir os índices numa
    coleção nova custa ~20 s, e separá-las dobraria o tempo do suite para provar duas
    propriedades da mesma operação.
    """
    from consulta_juridica.vectorstore import garantir_indices_payload

    garantir_colecao(client, nome=colecao, dim=DIM, nome_modelo="falso/denso")
    todos = {c.value for c in Campo}
    assert todos <= _esperar_schema(client, colecao, todos)
    assert garantir_indices_payload(client, colecao) == []


# --- indexação ------------------------------------------------------------------------


@pytest.fixture
def indexado(client, colecao_limpa, corpus_cdc, encoder):
    return indexar_norma(
        client, corpus_cdc, encoder, ChunkPorDispositivo(), URN_CDC, colecao=colecao_limpa
    )


def test_indexar_grava_um_ponto_por_chunk(indexado, client, colecao_limpa, corpus_cdc):
    from consulta_juridica.store import queries

    norma = queries.obter_norma(corpus_cdc, URN_CDC)
    disps = list(queries.iter_para_indexar(corpus_cdc, norma_urn=URN_CDC))
    esperado = len(list(ChunkPorDispositivo().chunks(disps, norma)))
    assert indexado.chunks == esperado == contar(client, colecao=colecao_limpa)


def test_payload_usa_os_nomes_de_vectorstore(indexado, client, colecao_limpa):
    """Se os nomes divergirem, o Qdrant não dá erro — devolve zero resultados."""
    ponto = client.scroll(colecao_limpa, limit=1, with_payload=True)[0][0]
    assert set(Campo) <= set(ponto.payload)


def test_payload_nao_carrega_o_texto(indexado, client, colecao_limpa):
    """O texto vive no SQLite. Duplicá-lo aqui cria duas cópias para divergir."""
    ponto = client.scroll(colecao_limpa, limit=1, with_payload=True)[0][0]
    assert "texto" not in ponto.payload


def test_sentinela_de_vigencia_chega_ao_payload(indexado, client, colecao_limpa):
    pontos = client.scroll(colecao_limpa, limit=100, with_payload=True)[0]
    valores = {p.payload[Campo.REVOGADO_EM.value] for p in pontos}
    assert SENTINELA_VIGENTE in valores, "dispositivo vigente precisa da sentinela"


def test_reindexar_apaga_antes_de_inserir(indexado, client, colecao_limpa, corpus_cdc, encoder):
    """Com upsert puro, um artigo que perdeu incisos mantém os chunks antigos no índice."""
    from consulta_juridica.models import TipoDispositivo as T
    from consulta_juridica.store import writer

    from .conftest import disp

    antes = contar(client, colecao=colecao_limpa)
    with db.transacao(corpus_cdc):
        writer.substituir_dispositivos(
            corpus_cdc,
            URN_CDC,
            [disp(URN_CDC, ["tit1", "art1"], T.ARTIGO, "Art. 1º", "sobrou só este", ordem=1)],
        )
    rel = indexar_norma(
        client, corpus_cdc, encoder, ChunkPorDispositivo(), URN_CDC, colecao=colecao_limpa
    )
    assert rel.removidos == antes
    assert contar(client, colecao=colecao_limpa) == 1


def test_apagar_norma_nao_toca_nas_outras(indexado, client, colecao_limpa):
    outra = "urn:lex:br:federal:lei:2002-01-10;10406"
    assert apagar_norma(client, outra, colecao=colecao_limpa) == 0
    assert contar(client, colecao=colecao_limpa) == indexado.chunks


def test_indexar_norma_inexistente_levanta(client, colecao_limpa, conn, encoder):
    with pytest.raises(NormaNaoEncontrada):
        indexar_norma(client, conn, encoder, ChunkPorDispositivo(), URN_CDC, colecao=colecao_limpa)


# --- pipeline -------------------------------------------------------------------------


def test_reindexar_le_so_do_sqlite():
    """A assinatura é a prova de que o índice é derivado: sem `Path`, sem `Fonte`."""
    import inspect

    params = inspect.signature(pipeline.reindexar).parameters
    assert not {"caminhos", "fontes", "destino"} & set(params)
    anotacoes = {str(p.annotation) for p in params.values()}
    assert not any("Path" in a or "Fonte" in a for a in anotacoes)


def test_reindexar_cobre_todas_as_normas_ingeridas(client, colecao_limpa, corpus_cdc, encoder):
    from datetime import UTC, datetime

    from consulta_juridica.store import writer

    with db.transacao(corpus_cdc):
        writer.registrar_ingestao(corpus_cdc, URN_CDC, "a" * 64, datetime.now(UTC))
    rel = pipeline.reindexar(
        corpus_cdc, client, encoder, ChunkPorDispositivo(), colecao=colecao_limpa
    )
    assert rel.normas == 1 and rel.chunks > 0


def test_ingerir_grava_e_resolve_remissoes(tmp_path: Path, conn: sqlite3.Connection):
    """Estágio 2 ponta a ponta, sem rede e sem Qdrant."""
    from consulta_juridica.ingest.fontes import salvar
    from consulta_juridica.store import queries

    from .conftest import URN_CF, doc_html, html_planalto

    caminho = salvar(
        doc_html(URN_CF, html_planalto(["Art. 1º A República.", "Art. 2º Na forma do art. 1º."])),
        tmp_path,
    )
    rel = pipeline.ingerir([caminho], conn)
    assert rel.normas == 1 and rel.dispositivos == 2
    assert queries.normas_indexadas(conn) == [(URN_CF, rel_sha(caminho))]
    assert any(r.resolvida for r in queries.remissoes_de(conn, f"{URN_CF}!art2"))


def rel_sha(caminho: Path) -> str:
    import json

    meta = caminho.with_suffix(caminho.suffix + ".meta.json")
    return json.loads(meta.read_text(encoding="utf-8"))["sha256"]


def test_baixar_nao_e_chamado_por_reindexar():
    """`reindexar` não pode alcançar a rede nem o disco de brutos."""
    import inspect

    fonte = inspect.getsource(pipeline.reindexar)
    assert "baixar" not in fonte and "carregar" not in fonte
