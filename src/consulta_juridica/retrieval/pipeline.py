"""Orquestração da recuperação: busca -> rerank -> expansão.

Dataclass com dependências injetadas, e não funções soltas, porque bge-m3 e o cross-encoder
somam alguns GB e segundos de carga: construídos uma vez, reutilizados a cada consulta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ..embedding import Encoder
from ..models import Trecho
from .busca import buscar, hidratar
from .expansao import Nivel, expandir
from .filtros import Criterios
from .rerank import Reranker, rerankear

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

#: Quantos candidatos a mais rerankear antes de expandir. A expansão funde os irmãos de um
#: mesmo artigo num trecho só, então pedir exatamente `k_final` ao reranker devolveria
#: menos que isso ao modelo. A folga só custa leitura de SQLite: o cross-encoder já
#: pontuou a lista inteira de qualquer jeito.
FOLGA_DEDUP: Final = 2


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
    k_prefetch: int = 150

    def recuperar(
        self,
        consulta: str,
        criterios: Criterios,
        *,
        k_busca: int = 50,
        k_final: int = 8,
    ) -> list[Trecho]:
        cands = buscar(
            self.client,
            self.encoder,
            consulta,
            criterios,
            colecao=self.colecao,
            k=k_busca,
            prefetch=self.k_prefetch,
        )
        # Antes do rerank: o payload não carrega texto, e o cross-encoder precisa lê-lo.
        cands = hidratar(self.conn, cands)
        if not cands:
            return []

        melhores = rerankear(self.reranker, consulta, cands, k=k_final * FOLGA_DEDUP)
        trechos = expandir(
            self.conn,
            melhores,
            nivel=self.nivel,
            data_referencia=criterios.data_referencia,
        )
        return trechos[:k_final]
