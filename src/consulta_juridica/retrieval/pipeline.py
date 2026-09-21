"""Orquestração da recuperação: busca -> rerank -> expansão.

Dataclass com dependências injetadas, e não funções soltas, porque bge-m3 e o cross-encoder
somam alguns GB e segundos de carga: construídos uma vez, reutilizados a cada consulta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..embedding import Encoder
from ..models import Trecho
from .expansao import Nivel
from .filtros import Criterios
from .rerank import Reranker

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class Recuperador:
    """Ponta a ponta da recuperação, sem nenhum LLM envolvido.

    Usado igualmente pela API e pelo eval — é por isso que não vive dentro de `api/`.
    """

    conn: sqlite3.Connection
    client: QdrantClient
    encoder: Encoder
    reranker: Reranker
    colecao: str
    nivel: Nivel = Nivel.ARTIGO

    def recuperar(
        self,
        consulta: str,
        criterios: Criterios,
        *,
        k_busca: int = 50,
        k_final: int = 8,
    ) -> list[Trecho]:
        raise NotImplementedError
