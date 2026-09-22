"""Contrato da coleção Qdrant: vocabulário compartilhado entre escrita e leitura.

`ingest.indexer` escreve o payload; `retrieval.busca` e `retrieval.filtros` leem e filtram
por ele. Se os nomes divergirem, o Qdrant não levanta erro — devolve zero resultados. Por
isso todo nome de vetor, campo e coleção nasce aqui e em nenhum outro lugar.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid5

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

    from .config import Settings

VETOR_DENSO: Final = "denso"
VETOR_ESPARSO: Final = "esparso"

#: `range(gt=hoje)` no Qdrant EXCLUI pontos sem o campo. Um dispositivo vigente tem
#: `revogado_em = None`, e sumiria do resultado. Gravamos esta sentinela no lugar.
SENTINELA_VIGENTE: Final[int] = date(9999, 12, 31).toordinal()

#: Namespace para os uuid5 dos pontos. Trocar este valor invalida o índice inteiro.
NAMESPACE_PONTO: Final = UUID("6f0f8d5e-6a3f-5f2a-9f1b-4a2c8e7d0b31")


class Campo(StrEnum):
    """Nomes dos campos de payload. Fonte única — não literalize estas strings."""

    DISPOSITIVO_ID = "dispositivo_id"
    NORMA_URN = "norma_urn"
    TIPO = "tipo"
    CAMINHO = "caminho"
    VIGENCIA_INICIO = "vigencia_inicio_dia"
    REVOGADO_EM = "revogado_em_dia"


#: Chaves do `metadata` da coleção. O Qdrant guarda esse dicionário no config da coleção
#: e o devolve em `get_collection` — é onde a procedência do índice pode viver sem um
#: ponto-sentinela nem uma tabela paralela.
META_MODELO: Final = "modelo_denso"
META_DIM: Final = "dim"

#: Timeout generoso: a indexação envia lotes grandes e o Qdrant pode estar otimizando
#: segmentos. O default de 5 s do cliente derruba a ingestão no meio.
TIMEOUT_S: Final = 120


def cliente(cfg: Settings) -> QdrantClient:
    """Abre o cliente Qdrant."""
    from qdrant_client import QdrantClient as _Cliente

    return _Cliente(url=cfg.qdrant_url, timeout=TIMEOUT_S)


def garantir_colecao(
    client: QdrantClient,
    *,
    nome: str,
    dim: int,
    nome_modelo: str,
    recriar: bool = False,
) -> None:
    """Cria a coleção se não existir, com vetor denso e esparso nomeados.

    O vetor esparso usa `Modifier.IDF`, porque a perna esparsa é BM25 e o IDF é aplicado
    server-side pelo Qdrant. (Se um dia a perna esparsa passar a vir dos pesos lexicais do
    bge-m3, o IDF precisa ser DESLIGADO — aqueles pesos já são ponderados.)

    Levanta `ColecaoIncompativel` se a coleção existente foi indexada com outro modelo ou
    outra dimensão: seguir adiante degradaria a recuperação sem sintoma visível.
    """
    from qdrant_client import models

    from .errors import ColecaoIncompativel

    if recriar and client.collection_exists(nome):
        client.delete_collection(nome)

    if client.collection_exists(nome):
        atual = metadados_colecao(client, nome)
        esperado = {META_MODELO: nome_modelo, META_DIM: str(dim)}
        if atual != esperado:
            raise ColecaoIncompativel(
                f"coleção {nome!r} foi construída com {atual}, mas a configuração atual "
                f"pede {esperado}. Reindexe com --recriar; misturar os dois produz "
                f"recuperação degradada sem erro visível."
            )
        garantir_indices_payload(client, nome)
        return

    client.create_collection(
        nome,
        vectors_config={
            VETOR_DENSO: models.VectorParams(size=dim, distance=models.Distance.COSINE)
        },
        # `Modifier.IDF` é obrigatório, não afinação: o BM25 do fastembed entrega
        # frequência de termo crua e delega o IDF ao servidor. Sem isto, termo comum pesa
        # igual a termo raro e a perna esparsa vira contagem de palavra.
        sparse_vectors_config={
            VETOR_ESPARSO: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
        metadata={META_MODELO: nome_modelo, META_DIM: str(dim)},
    )
    garantir_indices_payload(client, nome)


#: Tipo de índice por campo. `range` sobre inteiro exige INTEGER; `match` exige KEYWORD.
_SCHEMA_PAYLOAD: Final[dict[str, str]] = {
    Campo.NORMA_URN: "keyword",
    Campo.DISPOSITIVO_ID: "keyword",
    Campo.TIPO: "keyword",
    Campo.CAMINHO: "keyword",
    Campo.VIGENCIA_INICIO: "integer",
    Campo.REVOGADO_EM: "integer",
}


def garantir_indices_payload(client: QdrantClient, nome: str) -> list[str]:
    """Cria os payload indexes que faltam; devolve os nomes criados.

    Sem eles o filtro não usa o HNSW filtrável. Os dois campos de data são os que mais
    importam: sem índice, o filtro de vigência degrada para varredura e a consulta com
    `data_referencia` fica lenta na proporção do corpus.

    Duas decisões, as duas medidas:

    - **Só o que falta.** Recriar um índice existente não é no-op: o Qdrant reconstrói, e
      medi 3,5 s por campo. Como `garantir_colecao` roda em toda reindexação, isso eram
      21 s de espera pura a cada execução.
    - **`wait=False`.** A construção do índice é assíncrona por natureza e ninguém
      consulta no instante seguinte — a indexação gasta minutos codificando logo depois.
      Esperar custava os mesmos 21 s para não mudar nada.
    """
    existentes = set(client.get_collection(nome).payload_schema or {})
    criados = []
    for campo, tipo in _SCHEMA_PAYLOAD.items():
        if campo.value in existentes:
            continue
        client.create_payload_index(nome, field_name=campo.value, field_schema=tipo, wait=False)
        criados.append(campo.value)
    return criados


def metadados_colecao(client: QdrantClient, nome: str) -> dict[str, str]:
    """Modelo e dimensão com que a coleção foi construída, para a checagem de drift."""
    meta = getattr(client.get_collection(nome).config, "metadata", None) or {}
    return {str(k): str(v) for k, v in meta.items()}


def id_ponto(dispositivo_id: str) -> UUID:
    """ID determinístico do ponto.

    O Qdrant só aceita uuid ou int, e os IDs de domínio são URNs. Derivar por uuid5 torna
    o upsert idempotente — sem isso, cada reindexação duplicaria o corpus.
    """
    return uuid5(NAMESPACE_PONTO, dispositivo_id)


def dia(d: date) -> int:
    """Data -> inteiro ordinal, como gravado no payload."""
    return d.toordinal()


def dia_ou_sentinela(d: date | None) -> int:
    """`revogado_em` -> inteiro, usando a sentinela quando o dispositivo segue vigente."""
    return SENTINELA_VIGENTE if d is None else d.toordinal()
