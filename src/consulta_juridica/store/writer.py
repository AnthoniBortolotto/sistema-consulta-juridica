"""Escrita no SQLite. Só a ingestão importa este módulo."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ..models import Dispositivo, Norma, Remissao


def upsert_norma(conn: sqlite3.Connection, norma: Norma) -> None:
    """Insere ou atualiza a norma."""
    raise NotImplementedError


def substituir_dispositivos(
    conn: sqlite3.Connection, norma_urn: str, disps: Sequence[Dispositivo]
) -> int:
    """Apaga e reinsere a árvore inteira da norma; devolve a contagem gravada.

    Substituição, não merge: um dispositivo que sumiu da nova redação precisa sumir daqui,
    senão sobrevive como texto fantasma que ninguém vai notar.
    """
    raise NotImplementedError


def upsert_remissoes(conn: sqlite3.Connection, remissoes: Sequence[Remissao]) -> int:
    """Grava as remissões extraídas."""
    raise NotImplementedError


def resolver_remissoes_pendentes(conn: sqlite3.Connection) -> int:
    """Liga `destino_urn` a um `destino_id` existente; devolve quantas resolveu.

    Roda depois da ingestão, porque uma remissão pode apontar para norma ainda não ingerida.
    """
    raise NotImplementedError


def registrar_ingestao(
    conn: sqlite3.Connection, norma_urn: str, sha256: str, quando: datetime
) -> None:
    """Anota a procedência da ingestão."""
    raise NotImplementedError
