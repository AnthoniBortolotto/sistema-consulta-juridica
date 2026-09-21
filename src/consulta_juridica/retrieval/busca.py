"""Busca híbrida no Qdrant.

Não há módulo de fusão neste projeto: o RRF roda server-side, via `query_points` com dois
`prefetch` (denso e esparso) e `FusionQuery`. Reimplementar a fusão no cliente só
adicionaria uma segunda fonte de verdade para o ranking.

Atenção: o `query_filter` NÃO desce automaticamente para os prefetch. O filtro precisa ser
repetido dentro de cada um, senão a vigência é aplicada só depois da fusão — sobre uma
lista de candidatos que já veio contaminada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from ..embedding import Encoder
from ..models import ChunkPayload
from .filtros import Criterios

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class Candidato:
    """Resultado cru da busca, antes do rerank e da expansão."""

    chunk_id: UUID
    dispositivo_id: str
    texto: str
    payload: ChunkPayload
    score: float


def buscar(
    client: QdrantClient,
    encoder: Encoder,
    consulta: str,
    criterios: Criterios,
    *,
    colecao: str,
    k: int = 50,
    prefetch: int = 150,
) -> list[Candidato]:
    """Denso + esparso, fundidos por RRF, com filtro aplicado nos dois prefetch."""
    raise NotImplementedError
