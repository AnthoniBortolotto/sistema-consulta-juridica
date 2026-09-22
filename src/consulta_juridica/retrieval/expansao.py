"""Parent-document retrieval: candidato -> trecho legível.

Um inciso recuperado isoladamente costuma ser ininteligível sem o caput. Como o Qdrant não
tem join, a reconstrução acontece aqui, contra o SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from ..models import Dispositivo, TipoDispositivo, Trecho, linha_dispositivo
from ..store import queries
from ..urn import url_planalto
from .busca import Candidato

#: Agrupamentos que `Nivel.SECAO` aceita como destino: vale o mais próximo, e nem toda
#: norma usa os dois.
_AGRUPAMENTOS_SECAO = frozenset({TipoDispositivo.SUBSECAO, TipoDispositivo.SECAO})


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

    O nó de destino entra sempre, mesmo revogado — ele é o que a busca escolheu, e numa
    consulta com `incluir_revogados` é justamente o que se quer ler. Quem passa pelo filtro
    de vigência são os descendentes.
    """
    urls: dict[str, str] = {}
    trechos: list[Trecho] = []
    for c in cands:
        alvo = _alvo(conn, c.dispositivo_id, nivel)
        if alvo is None:
            continue
        trechos.append(
            Trecho(
                dispositivo_id=alvo.id,
                norma_urn=alvo.norma_urn,
                rotulo_completo=queries.rotulo_completo(conn, alvo.id),
                texto=_montar_texto(
                    conn, alvo, data_referencia, max_chars, descendentes=nivel is not Nivel.NENHUM
                ),
                fonte_url=_fonte_url(conn, alvo.norma_urn, urls),
                score=c.score_rerank if c.score_rerank is not None else c.score,
                score_fusao=c.score,
                score_rerank=c.score_rerank,
            )
        )
    return trechos


def _alvo(conn: sqlite3.Connection, dispositivo_id: str, nivel: Nivel) -> Dispositivo | None:
    """O dispositivo cujo texto vai ao modelo.

    Cada nível degrada para o de baixo quando não encontra destino: dispositivo fora de
    artigo (um capítulo), ou norma que não usa seções. Degradar é melhor que devolver
    nada — o candidato foi recuperado, então há o que mostrar.
    """
    atual = queries.obter_dispositivo(conn, dispositivo_id)
    if atual is None or nivel is Nivel.NENHUM:
        return atual

    if nivel is Nivel.SECAO:
        for anc in reversed(queries.ancestrais(conn, dispositivo_id)):
            if anc.tipo in _AGRUPAMENTOS_SECAO:
                return anc
    return queries.artigo_ancestral(conn, dispositivo_id) or atual


def _montar_texto(
    conn: sqlite3.Connection,
    alvo: Dispositivo,
    data_referencia: date,
    max_chars: int,
    *,
    descendentes: bool,
) -> str:
    """Nó de destino mais os descendentes VIGENTES, em ordem de documento.

    `descendentes=False` é o que faz `Nivel.NENHUM` significar "nenhuma expansão": sem
    isso, o artigo recuperado pelo caput viria com os incisos pendurados, que é exatamente
    `ChunkPorArtigo` — e o eval acharia estar medindo a recuperação crua.

    O corte por `max_chars` é sempre em fronteira de dispositivo: um artigo cortado no meio
    de uma frase chegaria ao modelo como texto legal mutilado, e a citação que saísse dali
    não bateria com a fonte. Se o próprio nó de destino já estoura o limite, ele vai
    inteiro assim mesmo — nunca se corta o texto que a busca escolheu.
    """
    partes = [alvo]
    if descendentes:
        partes += queries.subarvore(conn, alvo.id, data_referencia=data_referencia)
    linhas: list[str] = []
    total = 0
    for p in partes:
        if not p.texto.strip():
            continue
        linha = linha_dispositivo(p)
        if linhas and total + len(linha) + 1 > max_chars:
            break
        linhas.append(linha)
        total += len(linha) + 1
    return "\n".join(linhas)


def _fonte_url(conn: sqlite3.Connection, norma_urn: str, cache: dict[str, str]) -> str:
    """URL da norma para o usuário conferir a citação. Uma consulta por norma, não por trecho."""
    if norma_urn not in cache:
        norma = queries.obter_norma(conn, norma_urn)
        if norma is not None:
            cache[norma_urn] = norma.fonte_url
        else:
            # O dispositivo existe e a norma não: corpus inconsistente. O catálogo de
            # `urn` cobre o corpus atual; fora dele, melhor trecho sem link que exceção.
            try:
                cache[norma_urn] = url_planalto(norma_urn)
            except ValueError:
                cache[norma_urn] = ""
    return cache[norma_urn]


def deduplicar(trechos: Sequence[Trecho]) -> list[Trecho]:
    """Funde trechos repetidos: dois incisos do mesmo artigo expandem para o mesmo texto.

    Preserva a ordem de entrada — que é a do ranking — e mantém o melhor score de cada
    grupo. Reordenar aqui desfaria o trabalho do reranker.
    """
    por_id: dict[str, Trecho] = {}
    for t in trechos:
        anterior = por_id.get(t.dispositivo_id)
        if anterior is None:
            por_id[t.dispositivo_id] = t
            continue
        anterior.score = max(anterior.score, t.score)
        anterior.score_fusao = max(anterior.score_fusao or 0.0, t.score_fusao or 0.0)
        if t.score_rerank is not None:
            anterior.score_rerank = max(anterior.score_rerank or t.score_rerank, t.score_rerank)
    return list(por_id.values())
