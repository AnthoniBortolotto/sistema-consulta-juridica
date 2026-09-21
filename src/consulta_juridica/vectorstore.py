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


def cliente(cfg: Settings) -> QdrantClient:
    """Abre o cliente Qdrant."""
    raise NotImplementedError


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
    raise NotImplementedError


def garantir_indices_payload(client: QdrantClient, nome: str) -> None:
    """Cria os payload indexes. Sem eles o filtro não usa o HNSW filtrável."""
    raise NotImplementedError


def metadados_colecao(client: QdrantClient, nome: str) -> dict[str, str]:
    """Modelo e dimensão com que a coleção foi construída, para a checagem de drift."""
    raise NotImplementedError


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
