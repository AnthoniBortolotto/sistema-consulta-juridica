"""Fixtures compartilhadas."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from qdrant_client.models import SparseVector

from consulta_juridica.embedding import Encoder, Vetores
from consulta_juridica.models import Dispositivo, Norma, TipoDispositivo
from consulta_juridica.store import db, writer
from consulta_juridica.urn import montar_caminho, montar_urn_dispositivo

DIR_FIXTURES = Path(__file__).parent / "fixtures"

URN_CDC = "urn:lex:br:federal:lei:1990-09-11;8078"
URN_CF = "urn:lex:br:federal:constituicao:1988-10-05;1988"


@pytest.fixture
def data_ref() -> date:
    """Data de referência fixa. Nunca `date.today()` em teste — o resultado mudaria sozinho."""
    return date(2026, 1, 1)


@pytest.fixture
def fixture_cf88_art6() -> bytes:
    """Recorte real do Planalto: as três redações do art. 6º.

    Devolve BYTES, não texto: quem decide o encoding é `DocumentoBruto.texto()`, e é
    justamente essa detecção que precisa estar no caminho do teste. A fixture foi salva
    em utf-8 quando foi criada; a fonte é cp1252. Decodificar com o errado não levanta
    exceção, só corrompe o texto — foi assim que estes testes falharam da primeira vez.
    """
    return (DIR_FIXTURES / "cf88_art6.htm").read_bytes()


def html_planalto(blocos: list[str], *, riscados: list[str] | None = None) -> str:
    """HTML no formato do Planalto: um `<p>` por dispositivo, rótulo abrindo o bloco.

    Sintético, e só para a estrutura que a fixture não cobre — ela é o recorte de um
    artigo só. Tudo que depende da irregularidade da marcação real usa a fixture.
    """
    partes = [f"<p><font face='Arial'>{b}</font></p>" for b in blocos]
    partes += [f"<p><strike>{b}</strike></p>" for b in (riscados or [])]
    return f"<html><body>{''.join(partes)}</body></html>"


def doc_html(urn: str, html: str | bytes, *, url: str = "https://www.planalto.gov.br/x.htm"):
    """Empacota HTML como `DocumentoBruto`, para chamar o parser direto.

    Texto vai como cp1252, que é o encoding da fonte real; bytes passam intactos.
    """
    from consulta_juridica.ingest.fontes import DocumentoBruto, sha256_de

    conteudo = html if isinstance(html, bytes) else html.encode("cp1252", errors="replace")
    return DocumentoBruto(
        urn=urn,
        url=url,
        conteudo=conteudo,
        content_type="text/html",
        sha256=sha256_de(conteudo),
        baixado_em=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """SQLite em memória com o schema aplicado."""
    c = db.conectar(Path(db.EM_MEMORIA))
    db.aplicar_schema(c)
    yield c
    c.close()


def disp(
    norma_urn: str,
    segmentos: list[str],
    tipo: TipoDispositivo,
    rotulo: str,
    texto: str,
    *,
    ordem: int = 1,
    parent: list[str] | None = None,
    vigencia_inicio: date | None = None,
    revogado_em: date | None = None,
) -> Dispositivo:
    """Monta um Dispositivo pelos segmentos do caminho, derivando id e parent_id."""
    caminho = montar_caminho(segmentos)
    return Dispositivo(
        id=montar_urn_dispositivo(norma_urn, caminho),
        norma_urn=norma_urn,
        parent_id=(
            montar_urn_dispositivo(norma_urn, montar_caminho(parent)) if parent else None
        ),
        tipo=tipo,
        rotulo=rotulo,
        ordem=ordem,
        caminho=caminho,
        texto=texto,
        vigencia_inicio=vigencia_inicio,
        revogado_em=revogado_em,
    )


@pytest.fixture
def norma_cdc() -> Norma:
    return Norma(
        urn=URN_CDC,
        tipo="lei",
        numero="8078",
        ano=1990,
        data_publicacao=date(1990, 9, 11),
        apelido="CDC",
        fonte_url="https://www.planalto.gov.br/ccivil_03/leis/l8078.htm",
        sha256_origem="0" * 64,
    )

@pytest.fixture
def arvore_cdc() -> list[Dispositivo]:
    """Art. 6º do CDC, reduzido: caput mais cinco incisos, um revogado e um em vacatio.

    É o formato que a expansão e o chunking precisam exercitar — o inciso VIII isolado é
    ininteligível sem o caput, e o inciso revogado é o que não pode voltar nem pela porta
    do SQLite nem dentro de um chunk por artigo.
    """
    T = TipoDispositivo
    return [
        disp(URN_CDC, ["tit1"], T.TITULO, "Título I", "Dos Direitos do Consumidor", ordem=1),
        disp(URN_CDC, ["tit1", "cap3"], T.CAPITULO, "Capítulo III", "Dos Direitos Básicos",
             ordem=1, parent=["tit1"]),
        disp(URN_CDC, ["tit1", "cap3", "art6"], T.ARTIGO, "Art. 6º",
             "São direitos básicos do consumidor:", ordem=1, parent=["tit1", "cap3"]),
        # ordem fora de sequência de propósito: o inciso X não pode vir antes do II
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc10"], T.INCISO, "X",
             "a adequada e eficaz prestação dos serviços públicos em geral.",
             ordem=10, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc2"], T.INCISO, "II",
             "a educação e divulgação sobre o consumo adequado;",
             ordem=2, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc8"], T.INCISO, "VIII",
             "a facilitação da defesa de seus direitos, inclusive com a inversão do ônus "
             "da prova, a seu favor, no processo civil;",
             ordem=8, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc4"], T.INCISO, "IV",
             "inciso hipotético revogado, para o teste de vigência;",
             ordem=4, parent=["tit1", "cap3", "art6"], revogado_em=date(2020, 1, 1)),
        # vacatio legis: publicado, sem vigência na data de referência dos testes
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc11"], T.INCISO, "XI",
             "inciso hipotético que só entra em vigor em 2030;",
             ordem=11, parent=["tit1", "cap3", "art6"], vigencia_inicio=date(2030, 1, 1)),
    ]


@pytest.fixture
def corpus_cdc(
    conn: sqlite3.Connection, norma_cdc: Norma, arvore_cdc: list[Dispositivo]
) -> sqlite3.Connection:
    """A mesma árvore, já gravada no SQLite."""
    with db.transacao(conn):
        writer.upsert_norma(conn, norma_cdc)
        writer.substituir_dispositivos(conn, URN_CDC, arvore_cdc)
    return conn


# --- Qdrant ---------------------------------------------------------------------------
#
# Ficam aqui, e não no módulo de teste que as criou primeiro, porque indexação e
# recuperação precisam exatamente do mesmo cenário: a escrita e a leitura do índice têm de
# ser exercitadas contra a mesma coleção e o mesmo payload, senão o teste de uma não
# protege a outra.

DIM = 8


class EncoderFalso(Encoder):
    """Vetores determinísticos e baratos.

    Determinístico, não aleatório: um teste que falha tem de falhar sempre. O vetor é
    derivado do hash do texto, então textos diferentes dão vetores diferentes — é só
    disso que a indexação precisa. A qualidade dos vetores é problema do eval, não de
    teste unitário; carregar o bge-m3 aqui custaria minutos para não proteger nada.
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


@pytest.fixture
def encoder() -> Encoder:
    return EncoderFalso()


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


def colecao_de_sessao(client, nome: str) -> Iterator[str]:
    """Coleção com os índices de payload já construídos, para a sessão inteira.

    Medido: construir os 6 índices de payload custa ~21 s, e o `wait=False` só adia esse
    custo para o primeiro `upsert` síncrono. Pagar uma vez por sessão em vez de uma vez
    por teste tirou o suite de 4 min para segundos.

    Gerador, para o módulo de teste consumir com `yield from` numa fixture de sessão com
    nome próprio — duas coleções homônimas em módulos diferentes se atropelariam.
    """
    from qdrant_client import models

    from consulta_juridica.vectorstore import VETOR_DENSO, garantir_colecao

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


def esvaziar(client, nome: str) -> str:
    """Apaga todos os pontos da coleção, preservando os índices de payload."""
    from qdrant_client import models

    client.delete(nome, points_selector=models.FilterSelector(filter=models.Filter()), wait=True)
    return nome


def id_cdc(*segmentos: str) -> str:
    """ID canônico de um dispositivo da árvore de `arvore_cdc`."""
    return montar_urn_dispositivo(URN_CDC, montar_caminho(list(segmentos)))


def candidatos_de(
    arvore: list[Dispositivo], norma: Norma, ids: Sequence[str], scores: Sequence[float] = ()
):
    """Candidatos como a busca os devolveria, derivados dos chunks de verdade.

    Deriva payload e texto de `ChunkPorDispositivo` em vez de montá-los à mão: o que a
    expansão e o rerank leem é o payload REAL, e um payload fabricado no teste esconderia
    justamente a divergência entre o que a ingestão grava e o que a recuperação espera.
    """
    from consulta_juridica.ingest.chunking import ChunkPorDispositivo
    from consulta_juridica.retrieval.busca import Candidato

    por_id = {c.payload.dispositivo_id: c for c in ChunkPorDispositivo().chunks(arvore, norma)}
    return [
        Candidato(
            chunk_id=por_id[i].id,
            dispositivo_id=i,
            texto=por_id[i].texto,
            payload=por_id[i].payload,
            score=s,
        )
        for i, s in zip(ids, scores or [1.0 / (n + 1) for n in range(len(ids))], strict=True)
    ]
