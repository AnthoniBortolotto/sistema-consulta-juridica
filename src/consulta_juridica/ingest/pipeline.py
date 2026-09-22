"""Estágios re-executáveis da ingestão.

Três estágios separados porque falham por motivos diferentes e têm custos diferentes:
baixar depende de rede, ingerir depende do parser, reindexar depende dos modelos.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..embedding import Encoder
from ..store import db, queries, writer
from .chunking import Estrategia
from .fontes import Fonte, carregar, salvar
from .indexer import RelatorioIndexacao, indexar_norma
from .parser import escolher_parser

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class RelatorioIngestao:
    normas: int = 0
    dispositivos: int = 0
    remissoes: int = 0
    avisos: list[str] = field(default_factory=list)


def baixar(fontes: Sequence[Fonte], destino: Path, *, limite: int | None = None) -> list[Path]:
    """Estágio 1: fontes -> `data/raw/`. Devolve os caminhos salvos."""
    salvos: list[Path] = []
    for fonte in fontes:
        for ref in fonte.listar():
            if limite is not None and len(salvos) >= limite:
                return salvos
            salvos.append(salvar(fonte.baixar(ref), destino))
    return salvos


def ingerir(caminhos: Sequence[Path], conn: sqlite3.Connection) -> RelatorioIngestao:
    """Estágio 2: brutos -> parse -> SQLite. Não toca no Qdrant.

    Uma transação por norma, não uma para tudo: parse que falha na terceira norma não
    pode desfazer as duas que já entraram — reparsear o corpus inteiro por causa de um
    caso de canto é o que torna o refino do parser insuportável.
    """
    rel = RelatorioIngestao()
    for caminho in caminhos:
        doc = carregar(caminho)
        r = escolher_parser(doc).parse(doc)
        with db.transacao(conn):
            writer.upsert_norma(conn, r.norma)
            rel.dispositivos += writer.substituir_dispositivos(
                conn, r.norma.urn, r.dispositivos
            )
            rel.remissoes += writer.upsert_remissoes(conn, r.remissoes)
            writer.registrar_ingestao(conn, r.norma.urn, doc.sha256, datetime.now(UTC))
        rel.normas += 1
        rel.avisos += [f"{r.norma.apelido or r.norma.urn}: {a}" for a in r.avisos]

    # Depois de todas: uma remissão pode apontar para norma que só entrou agora.
    with db.transacao(conn):
        writer.resolver_remissoes_pendentes(conn)
    return rel


def reindexar(
    conn: sqlite3.Connection,
    client: QdrantClient,
    encoder: Encoder,
    estrategia: Estrategia,
    *,
    colecao: str,
    normas: Sequence[str] | None = None,
) -> RelatorioIndexacao:
    """Estágio 3: SQLite -> Qdrant.

    A assinatura é a prova de que o índice é derivado: não recebe `Path` nem `Fonte`. Se um
    dia precisar de qualquer um dos dois, a propriedade "o Qdrant é reconstruível a partir
    do SQLite" foi quebrada.
    """
    alvos = list(normas) if normas else [urn for urn, _ in queries.normas_indexadas(conn)]
    total = RelatorioIndexacao()
    for urn in alvos:
        total.somar(
            indexar_norma(client, conn, encoder, estrategia, urn, colecao=colecao)
        )
    return total
