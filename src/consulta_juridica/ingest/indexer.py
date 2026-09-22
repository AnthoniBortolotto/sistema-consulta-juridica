"""Escrita no Qdrant. Read-path correspondente: `retrieval.busca`.

Vocabulário (nomes de coleção, vetores e campos) vem de `vectorstore` — nunca literalizar
essas strings aqui.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..embedding import Encoder
from ..errors import NormaNaoEncontrada
from ..models import Chunk
from ..store import queries
from ..vectorstore import VETOR_DENSO, VETOR_ESPARSO, Campo
from .chunking import Estrategia

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class RelatorioIndexacao:
    normas: int = 0
    chunks: int = 0
    removidos: int = 0
    duracao_s: float = 0.0

    def somar(self, outro: RelatorioIndexacao) -> None:
        self.normas += outro.normas
        self.chunks += outro.chunks
        self.removidos += outro.removidos
        self.duracao_s += outro.duracao_s


def _filtro_norma(norma_urn: str):
    from qdrant_client import models

    return models.Filter(
        must=[
            models.FieldCondition(
                key=Campo.NORMA_URN.value, match=models.MatchValue(value=norma_urn)
            )
        ]
    )


def _ponto(chunk: Chunk, vetores):
    from qdrant_client import models

    return models.PointStruct(
        id=str(chunk.id),
        vector={VETOR_DENSO: vetores.denso, VETOR_ESPARSO: vetores.esparso},
        # O TEXTO NÃO VAI PARA O PAYLOAD. Ele vive no SQLite, que é a fonte da verdade;
        # duplicá-lo aqui criaria duas cópias para divergir na próxima reingestão, e é
        # exatamente a desnormalização que a armadilha 4 manda evitar.
        payload=chunk.payload.model_dump(mode="json"),
    )


def indexar_norma(
    client: QdrantClient,
    conn: sqlite3.Connection,
    encoder: Encoder,
    estrategia: Estrategia,
    norma_urn: str,
    *,
    colecao: str,
    lote: int = 512,
) -> RelatorioIndexacao:
    """Reindexa uma norma inteira: APAGA os pontos dela e insere os novos.

    Apagar antes é obrigatório, não otimização evitada: com upsert puro, um artigo que
    perdeu incisos na nova redação mantém os chunks antigos no índice. Texto revogado
    sobrevivendo como recuperável é o pior defeito possível neste domínio.
    """
    t0 = time.perf_counter()
    norma = queries.obter_norma(conn, norma_urn)
    if norma is None:
        raise NormaNaoEncontrada(norma_urn)

    disps = list(queries.iter_para_indexar(conn, norma_urn=norma_urn))
    chunks = list(estrategia.chunks(disps, norma))

    removidos = apagar_norma(client, norma_urn, colecao=colecao)

    gravados = 0
    for i in range(0, len(chunks), lote):
        fatia = chunks[i : i + lote]
        vetores = encoder.documentos([c.texto_indexado for c in fatia], lote=len(fatia))
        pontos = [_ponto(c, v) for c, v in zip(fatia, vetores, strict=True)]
        client.upsert(colecao, points=pontos, wait=True)
        gravados += len(pontos)

    return RelatorioIndexacao(
        normas=1,
        chunks=gravados,
        removidos=removidos,
        duracao_s=time.perf_counter() - t0,
    )


def apagar_norma(client: QdrantClient, norma_urn: str, *, colecao: str) -> int:
    """Remove todos os pontos de uma norma; devolve quantos apagou."""
    from qdrant_client import models

    filtro = _filtro_norma(norma_urn)
    quantos = client.count(colecao, count_filter=filtro, exact=True).count
    if quantos:
        client.delete(colecao, points_selector=models.FilterSelector(filter=filtro), wait=True)
    return quantos


def contar(client: QdrantClient, *, colecao: str, norma_urn: str | None = None) -> int:
    """Pontos na coleção, opcionalmente de uma norma só. Usado pelo CLI e pelo eval."""
    filtro = _filtro_norma(norma_urn) if norma_urn else None
    return client.count(colecao, count_filter=filtro, exact=True).count
