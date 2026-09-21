"""Leitura do SQLite. Usado por recuperação, geração e eval.

Regra estrutural: nenhuma função daqui chama `datetime.now()`. A data de referência entra
pela borda e viaja explícita — consultar o direito vigente à época de um fato é requisito,
não caso de canto.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date

from ..models import Dispositivo, Norma, Remissao


def obter_norma(conn: sqlite3.Connection, urn: str) -> Norma | None:
    raise NotImplementedError


def obter_dispositivo(conn: sqlite3.Connection, id: str) -> Dispositivo | None:
    raise NotImplementedError


def obter_dispositivos(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Dispositivo]:
    """Busca em lote. A expansão resolve dezenas de IDs por consulta — evita N+1."""
    raise NotImplementedError


def ancestrais(conn: sqlite3.Connection, id: str) -> list[Dispositivo]:
    """Da raiz até o pai, em ordem. Base do rótulo completo e do texto contextualizado."""
    raise NotImplementedError


def subarvore(
    conn: sqlite3.Connection, id: str, *, data_referencia: date
) -> list[Dispositivo]:
    """Descendentes VIGENTES na data dada, em ordem de documento.

    `data_referencia` é obrigatório por um motivo concreto: o filtro do Qdrant já excluiu o
    inciso revogado, mas a expansão lê o artigo inteiro daqui. Sem o filtro repetido neste
    lado, os irmãos revogados voltam pelo SQLite e chegam ao modelo como direito vigente.
    """
    raise NotImplementedError


def artigo_ancestral(conn: sqlite3.Connection, id: str) -> Dispositivo | None:
    """Sobe até o artigo que contém o dispositivo. Núcleo do parent-document retrieval."""
    raise NotImplementedError


def rotulo_completo(conn: sqlite3.Connection, id: str) -> str:
    """"Lei 8.078/1990, Art. 6º, VIII" — o que o usuário vê e confere na fonte."""
    raise NotImplementedError


def remissoes_de(conn: sqlite3.Connection, id: str) -> list[Remissao]:
    """Remissões que partem do dispositivo (expansão por 1 hop)."""
    raise NotImplementedError


def iter_para_indexar(
    conn: sqlite3.Connection, *, norma_urn: str | None = None
) -> Iterator[Dispositivo]:
    """Fonte da reindexação. Lê só do SQLite — é o que torna o Qdrant descartável."""
    raise NotImplementedError


def normas_indexadas(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(urn, sha256_origem) do que já foi ingerido, para pular trabalho repetido."""
    raise NotImplementedError
