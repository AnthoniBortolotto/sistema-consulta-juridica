"""Escrita no SQLite. Só a ingestão importa este módulo."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ..models import Dispositivo, Norma, Remissao
from ..urn import SEPARADOR_CAMINHO


def _iso(valor: object) -> str | None:
    """Data -> texto ISO. As colunas de data são TEXT: ISO ordena igual a cronológico."""
    return None if valor is None else str(valor)


def upsert_norma(conn: sqlite3.Connection, norma: Norma) -> None:
    """Insere ou atualiza a norma."""
    conn.execute(
        """
        INSERT INTO norma (urn, tipo, numero, ano, data_publicacao, apelido, ementa,
                           fonte_url, sha256_origem)
        VALUES (:urn, :tipo, :numero, :ano, :data_publicacao, :apelido, :ementa,
                :fonte_url, :sha256_origem)
        ON CONFLICT(urn) DO UPDATE SET
            tipo            = excluded.tipo,
            numero          = excluded.numero,
            ano             = excluded.ano,
            data_publicacao = excluded.data_publicacao,
            apelido         = excluded.apelido,
            ementa          = excluded.ementa,
            fonte_url       = excluded.fonte_url,
            sha256_origem   = excluded.sha256_origem
        """,
        {
            "urn": norma.urn,
            "tipo": norma.tipo,
            "numero": norma.numero,
            "ano": norma.ano,
            "data_publicacao": _iso(norma.data_publicacao),
            "apelido": norma.apelido,
            "ementa": norma.ementa,
            "fonte_url": norma.fonte_url,
            "sha256_origem": norma.sha256_origem,
        },
    )


def substituir_dispositivos(
    conn: sqlite3.Connection, norma_urn: str, disps: Sequence[Dispositivo]
) -> int:
    """Apaga e reinsere a árvore inteira da norma; devolve a contagem gravada.

    Substituição, não merge: um dispositivo que sumiu da nova redação precisa sumir daqui,
    senão sobrevive como texto fantasma que ninguém vai notar.

    Duas consequências do DELETE que não são óbvias:

    - A FK de `remissao.origem_id` tem ON DELETE CASCADE, então as remissões que PARTEM
      desta norma somem junto. Correto: elas são reextraídas do texto novo.
    - `remissao.destino_id` não é FK. Uma remissão de OUTRA norma que apontava para um
      dispositivo daqui ficaria com um ID que não existe mais. Por isso o destino é
      desfeito abaixo, para `resolver_remissoes_pendentes` refazer o vínculo.
    """
    conn.execute("DELETE FROM dispositivo WHERE norma_urn = ?", (norma_urn,))
    conn.execute(
        """
        UPDATE remissao SET destino_id = NULL, resolvida = 0
         WHERE destino_id IS NOT NULL
           AND destino_id NOT IN (SELECT id FROM dispositivo)
        """
    )

    # Pai antes de filho, senão a FK auto-referente rejeita a inserção. A profundidade do
    # materialized path é a ordem topológica, e não depende de `disps` vir ordenado.
    ordenados = sorted(disps, key=lambda d: (d.caminho.count(SEPARADOR_CAMINHO), d.caminho))

    conn.executemany(
        """
        INSERT INTO dispositivo (id, norma_urn, parent_id, tipo, rotulo, ordem, caminho,
                                 texto, vigencia_inicio, revogado_em, nota_alteracao)
        VALUES (:id, :norma_urn, :parent_id, :tipo, :rotulo, :ordem, :caminho,
                :texto, :vigencia_inicio, :revogado_em, :nota_alteracao)
        """,
        [
            {
                "id": d.id,
                "norma_urn": d.norma_urn,
                "parent_id": d.parent_id,
                "tipo": d.tipo.value,
                "rotulo": d.rotulo,
                "ordem": d.ordem,
                "caminho": d.caminho,
                "texto": d.texto,
                "vigencia_inicio": _iso(d.vigencia_inicio),
                "revogado_em": _iso(d.revogado_em),
                "nota_alteracao": d.nota_alteracao,
            }
            for d in ordenados
        ],
    )
    return len(ordenados)


def upsert_remissoes(conn: sqlite3.Connection, remissoes: Sequence[Remissao]) -> int:
    """Grava as remissões extraídas; devolve quantas foram enviadas.

    Idempotente pela chave natural `ux_remissao` (origem, destino, texto): reingerir a
    mesma norma reescreve, não duplica.
    """
    if not remissoes:
        return 0
    conn.executemany(
        """
        INSERT INTO remissao (origem_id, destino_urn, destino_id, texto_original, resolvida)
        VALUES (:origem_id, :destino_urn, :destino_id, :texto_original, :resolvida)
        ON CONFLICT(origem_id, destino_urn, texto_original) DO UPDATE SET
            destino_id = excluded.destino_id,
            resolvida  = excluded.resolvida
        """,
        [
            {
                "origem_id": r.origem_id,
                "destino_urn": r.destino_urn,
                "destino_id": r.destino_id,
                "texto_original": r.texto_original,
                "resolvida": int(r.resolvida),
            }
            for r in remissoes
        ],
    )
    return len(remissoes)


def resolver_remissoes_pendentes(conn: sqlite3.Connection) -> int:
    """Liga `destino_urn` a um `destino_id` existente; devolve quantas resolveu.

    Roda depois da ingestão, porque uma remissão pode apontar para norma ainda não ingerida.

    Resolver é confirmar que o dispositivo endereçado existe. Remissão para uma norma
    inteira ("nos termos da Lei 8.078/1990") não tem fragmento, não casa com nenhum
    `dispositivo.id` e fica pendente de propósito: `destino_id` é dispositivo, não norma.
    """
    cur = conn.execute(
        """
        UPDATE remissao
           SET destino_id = destino_urn, resolvida = 1
         WHERE resolvida = 0
           AND destino_urn IN (SELECT id FROM dispositivo)
        """
    )
    return cur.rowcount


def registrar_ingestao(
    conn: sqlite3.Connection, norma_urn: str, sha256: str, quando: datetime
) -> None:
    """Anota a procedência da ingestão."""
    conn.execute(
        """
        INSERT INTO ingestao (norma_urn, sha256_origem, quando)
        VALUES (?, ?, ?)
        ON CONFLICT(norma_urn) DO UPDATE SET
            sha256_origem = excluded.sha256_origem,
            quando        = excluded.quando
        """,
        (norma_urn, sha256, quando.isoformat()),
    )
