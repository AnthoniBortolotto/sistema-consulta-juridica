"""Parent-document retrieval: candidato -> trecho legível.

Um inciso recuperado isoladamente costuma ser ininteligível sem o caput. Como o Qdrant não
tem join, a reconstrução acontece aqui, contra o SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from ..models import Trecho
from .busca import Candidato


class Nivel(StrEnum):
    """Até onde subir na hierarquia ao expandir."""

    NENHUM = "nenhum"
    ARTIGO = "artigo"
    SECAO = "secao"


def expandir(
    conn: sqlite3.Connection,
    cands: Sequence[Candidato],
    *,
    nivel: Nivel,
    data_referencia: date,
    max_chars: int = 8000,
) -> list[Trecho]:
    """Sobe do candidato até o nível pedido e monta o texto a enviar ao modelo.

    `data_referencia` é obrigatório e precisa ser o MESMO usado no filtro da busca. O filtro
    do Qdrant excluiu o inciso revogado, mas aqui lemos o artigo inteiro do SQLite: sem
    reaplicar a vigência, os irmãos revogados voltam pela porta dos fundos e chegam ao
    modelo como se fossem direito vigente. É o bug mais provável do sistema.
    """
    raise NotImplementedError


def deduplicar(trechos: Sequence[Trecho]) -> list[Trecho]:
    """Funde trechos repetidos: dois incisos do mesmo artigo expandem para o mesmo texto."""
    raise NotImplementedError
