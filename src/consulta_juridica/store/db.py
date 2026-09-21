"""Conexão e schema do SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def conectar(caminho: Path, *, somente_leitura: bool = False) -> sqlite3.Connection:
    """Abre a conexão.

    `somente_leitura=True` no caminho de consulta: recuperação e geração nunca escrevem,
    e deixar o banco impedir isso é mais barato que confiar na disciplina.
    """
    raise NotImplementedError


def aplicar_schema(conn: sqlite3.Connection) -> None:
    """Executa `schema.sql` (idempotente)."""
    raise NotImplementedError


@contextmanager
def transacao(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit no sucesso, rollback na exceção."""
    raise NotImplementedError
    yield conn  # pragma: no cover
