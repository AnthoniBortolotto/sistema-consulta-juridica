"""Escrita no Qdrant. Read-path correspondente: `retrieval.busca`.

Vocabulário (nomes de coleção, vetores e campos) vem de `vectorstore` — nunca literalizar
essas strings aqui.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..embedding import Encoder
from .chunking import Estrategia

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class RelatorioIndexacao:
    normas: int = 0
    chunks: int = 0
    removidos: int = 0
    duracao_s: float = 0.0


def indexar_norma(
    client: QdrantClient,
    conn: sqlite3.Connection,
    encoder: Encoder,
    estrategia: Estrategia,
    norma_urn: str,
    *,
    colecao: str,
    lote: int = 128,
) -> RelatorioIndexacao:
    """Reindexa uma norma inteira: APAGA os pontos dela e insere os novos.

    Apagar antes é obrigatório, não otimização evitada: com upsert puro, um artigo que
    perdeu incisos na nova redação mantém os chunks antigos no índice. Texto revogado
    sobrevivendo como recuperável é o pior defeito possível neste domínio.
    """
    raise NotImplementedError


def apagar_norma(client: QdrantClient, norma_urn: str, *, colecao: str) -> int:
    """Remove todos os pontos de uma norma; devolve quantos apagou."""
    raise NotImplementedError
