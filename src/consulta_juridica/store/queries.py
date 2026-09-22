"""Leitura do SQLite. Usado por recuperação, geração e eval.

Regra estrutural: nenhuma função daqui chama `datetime.now()`. A data de referência entra
pela borda e viaja explícita — consultar o direito vigente à época de um fato é requisito,
não caso de canto.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Final

from ..models import Dispositivo, Norma, Remissao, TipoDispositivo, ordem_documento
from ..urn import SEPARADOR_CAMINHO, SEPARADOR_VERSAO, rotulo_completo_de, sem_versao

#: Predicado de vigência, em um lugar só.
#:
#: **Precisa ser idêntico ao filtro do Qdrant em `retrieval.filtros`.** Se divergirem, o
#: índice e a expansão discordam sobre o que está em vigor, e o sintoma é um dispositivo
#: revogado chegando ao modelo pela porta do SQLite — sem exceção, sem log.
#:
#: `revogado_em > :ref` e não `>=`: a revogação produz efeito NA data, então o dispositivo
#: já não vale nesse dia. Corresponde ao `range(gt=ref)` do lado do Qdrant.
#: As colunas são TEXT em ISO-8601, cuja ordem lexicográfica é a cronológica.
PREDICADO_VIGENTE: Final = (
    "(d.vigencia_inicio IS NULL OR d.vigencia_inicio <= :ref) "
    "AND (d.revogado_em IS NULL OR d.revogado_em > :ref)"
)

_COLUNAS: Final = (
    "d.id, d.norma_urn, d.parent_id, d.tipo, d.rotulo, d.ordem, d.caminho, d.texto, "
    "d.vigencia_inicio, d.revogado_em, d.nota_alteracao"
)

#: Limite de parâmetros por statement no SQLite antigo. Lotes maiores são fatiados.
_LOTE: Final = 900


def _para_dispositivo(row: sqlite3.Row) -> Dispositivo:
    return Dispositivo.model_validate(dict(row))


def _para_norma(row: sqlite3.Row) -> Norma:
    return Norma.model_validate(dict(row))


def obter_norma(conn: sqlite3.Connection, urn: str) -> Norma | None:
    row = conn.execute("SELECT * FROM norma WHERE urn = ?", (urn,)).fetchone()
    return _para_norma(row) if row else None


def obter_dispositivo(conn: sqlite3.Connection, id: str) -> Dispositivo | None:
    row = conn.execute(f"SELECT {_COLUNAS} FROM dispositivo d WHERE d.id = ?", (id,)).fetchone()
    return _para_dispositivo(row) if row else None


def obter_dispositivos(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Dispositivo]:
    """Busca em lote. A expansão resolve dezenas de IDs por consulta — evita N+1."""
    encontrados: dict[str, Dispositivo] = {}
    unicos = list(dict.fromkeys(ids))
    for i in range(0, len(unicos), _LOTE):
        fatia = unicos[i : i + _LOTE]
        marcadores = ",".join("?" * len(fatia))
        rows = conn.execute(
            f"SELECT {_COLUNAS} FROM dispositivo d WHERE d.id IN ({marcadores})", fatia
        )
        for row in rows:
            encontrados[row["id"]] = _para_dispositivo(row)
    return encontrados


def ancestrais(conn: sqlite3.Connection, id: str) -> list[Dispositivo]:
    """Da raiz até o pai, em ordem. Base do rótulo completo e do texto contextualizado.

    Resolvido por prefixo do materialized path, não por CTE recursiva: a profundidade máxima
    é 8 e os prefixos são conhecidos antes da consulta, então é um `IN` de tamanho fixo.
    """
    atual = obter_dispositivo(conn, id)
    if atual is None:
        return []

    segs = atual.caminho.split(SEPARADOR_CAMINHO)
    prefixos = [SEPARADOR_CAMINHO.join(segs[: i + 1]) for i in range(len(segs) - 1)]
    if not prefixos:
        return []

    marcadores = ",".join("?" * len(prefixos))
    rows = conn.execute(
        f"SELECT {_COLUNAS} FROM dispositivo d "
        f"WHERE d.norma_urn = ? AND d.caminho IN ({marcadores})",
        [atual.norma_urn, *prefixos],
    )
    por_caminho = {row["caminho"]: _para_dispositivo(row) for row in rows}
    return [por_caminho[p] for p in prefixos if p in por_caminho]


def subarvore(conn: sqlite3.Connection, id: str, *, data_referencia: date) -> list[Dispositivo]:
    """Descendentes VIGENTES na data dada, em ordem de documento. Não inclui o próprio nó.

    `data_referencia` é obrigatório por um motivo concreto: o filtro do Qdrant já excluiu o
    inciso revogado, mas a expansão lê o artigo inteiro daqui. Sem o filtro repetido neste
    lado, os irmãos revogados voltam pelo SQLite e chegam ao modelo como direito vigente.
    """
    atual = obter_dispositivo(conn, id)
    if atual is None:
        return []

    rows = conn.execute(
        f"SELECT {_COLUNAS} FROM dispositivo d "
        f"WHERE d.norma_urn = :norma AND d.caminho LIKE :prefixo ESCAPE '\\' "
        f"AND {PREDICADO_VIGENTE}",
        {
            "norma": atual.norma_urn,
            # ESCAPE porque `_` é curinga do LIKE. Nenhum segmento usa `_` hoje, mas o
            # dia em que usar, o filtro passaria a casar demais — em silêncio.
            "prefixo": _escapar_like(atual.caminho + SEPARADOR_CAMINHO) + "%",
            "ref": data_referencia.isoformat(),
        },
    )
    return ordem_documento([_para_dispositivo(r) for r in rows])


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def redacoes(
    conn: sqlite3.Connection, id: str, *, data_referencia: date | None = None
) -> list[Dispositivo]:
    """Todas as redações de um dispositivo: a vigente e as superadas (`art6@1`, `art6@2`).

    `data_referencia` filtra pelas que estavam em vigor na data — e é por isso que esta
    função vive aqui e não no eval: o predicado de vigência tem UMA implementação, e uma
    segunda escrita em Python divergiria da primeira sem levantar exceção.

    O sufixo de versão é aplicado ao último segmento, que é o que `ingest.parser` produz:
    a redação superada é folha, não tem filhos para versionar em cascata.
    """
    base = sem_versao(id)
    sql = f"SELECT {_COLUNAS} FROM dispositivo d WHERE (d.id = :base OR d.id LIKE :versoes)"
    params: dict[str, str] = {"base": base, "versoes": f"{base}{SEPARADOR_VERSAO}%"}
    if data_referencia is not None:
        sql += f" AND {PREDICADO_VIGENTE}"
        params["ref"] = data_referencia.isoformat()
    return [_para_dispositivo(r) for r in conn.execute(sql + " ORDER BY d.id", params)]


def artigo_ancestral(conn: sqlite3.Connection, id: str) -> Dispositivo | None:
    """Sobe até o artigo que contém o dispositivo. Núcleo do parent-document retrieval.

    Devolve o próprio nó quando ele já é o artigo, e None para nó estrutural (um capítulo
    não está dentro de artigo nenhum).
    """
    atual = obter_dispositivo(conn, id)
    if atual is None:
        return None
    if atual.tipo is TipoDispositivo.ARTIGO:
        return atual
    for anc in reversed(ancestrais(conn, id)):
        if anc.tipo is TipoDispositivo.ARTIGO:
            return anc
    return None


def rotulo_completo(conn: sqlite3.Connection, id: str) -> str:
    """"Lei 8.078/1990, Art. 6º, VIII" — o que o usuário vê e confere na fonte.

    A formatação vive em `urn.rotulo_completo_de`, que o chunking também usa para gravar
    o rótulo no payload. Aqui só carregamos a cadeia do banco.
    """
    atual = obter_dispositivo(conn, id)
    if atual is None:
        return ""
    return rotulo_completo_de([*ancestrais(conn, id), atual])


def remissoes_de(conn: sqlite3.Connection, id: str) -> list[Remissao]:
    """Remissões que partem do dispositivo (expansão por 1 hop)."""
    rows = conn.execute(
        "SELECT origem_id, destino_urn, destino_id, texto_original, resolvida "
        "FROM remissao WHERE origem_id = ? ORDER BY id",
        (id,),
    )
    return [Remissao.model_validate(dict(r)) for r in rows]


def iter_para_indexar(
    conn: sqlite3.Connection, *, norma_urn: str | None = None
) -> Iterator[Dispositivo]:
    """Fonte da reindexação. Lê só do SQLite — é o que torna o Qdrant descartável.

    Não filtra vigência: o dispositivo revogado precisa estar no índice para a consulta
    com data retroativa encontrá-lo. Quem filtra é a consulta, não a indexação.
    """
    sql = f"SELECT {_COLUNAS} FROM dispositivo d"
    params: tuple[str, ...] = ()
    if norma_urn is not None:
        sql += " WHERE d.norma_urn = ?"
        params = (norma_urn,)
    sql += " ORDER BY d.norma_urn, d.caminho"

    for row in conn.execute(sql, params):
        yield _para_dispositivo(row)


def normas_indexadas(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(urn, sha256_origem) do que já foi ingerido, para pular trabalho repetido."""
    rows = conn.execute("SELECT norma_urn, sha256_origem FROM ingestao ORDER BY norma_urn")
    return [(r["norma_urn"], r["sha256_origem"]) for r in rows]
