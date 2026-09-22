"""Parent-document retrieval: candidato -> trecho legível.

Um inciso recuperado isoladamente costuma ser ininteligível sem o caput. Como o Qdrant não
tem join, a reconstrução acontece aqui, contra o SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Final

from ..models import Dispositivo, TipoDispositivo, Trecho, linha_dispositivo
from ..store import queries
from ..urn import url_planalto
from .busca import Candidato

#: Agrupamentos que `Nivel.SECAO` aceita como destino: vale o mais próximo, e nem toda
#: norma usa os dois.
_AGRUPAMENTOS_SECAO = frozenset({TipoDispositivo.SUBSECAO, TipoDispositivo.SECAO})

#: Marca de dispositivo omitido pelo corte de `max_chars`. Vai em linha própria, entre
#: dispositivos: o texto de cada um continua literal, então a citação segue conferível.
#: Sem a marca, o modelo leria dois incisos distantes como se fossem consecutivos.
MARCA_OMISSAO: Final = "[…]"


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

    **Candidatos que sobem para o mesmo destino viram UM trecho.** A fusão tem de acontecer
    aqui, antes da montagem do texto, e não depois: dois incisos do mesmo artigo precisam
    aparecer no MESMO texto, e fundir dois textos já montados obrigaria a jogar um fora —
    com ele, o inciso que só estava naquele.
    """
    alvos: dict[str, Dispositivo] = {}
    grupos: dict[str, list[Candidato]] = {}
    for c in cands:
        alvo = _alvo(conn, c.dispositivo_id, nivel)
        if alvo is None:
            continue
        alvos.setdefault(alvo.id, alvo)
        grupos.setdefault(alvo.id, []).append(c)

    urls: dict[str, str] = {}
    trechos: list[Trecho] = []
    for alvo_id, grupo in grupos.items():
        alvo = alvos[alvo_id]
        texto, dispositivos = _montar_texto(
            conn,
            alvo,
            data_referencia,
            max_chars,
            descendentes=nivel is not Nivel.NENHUM,
            obrigatorios=[c.dispositivo_id for c in grupo],
        )
        rerank = [c.score_rerank for c in grupo if c.score_rerank is not None]
        fusao = max(c.score for c in grupo)
        trechos.append(
            Trecho(
                dispositivo_id=alvo.id,
                norma_urn=alvo.norma_urn,
                rotulo_completo=queries.rotulo_completo(conn, alvo.id),
                texto=texto,
                dispositivos=dispositivos,
                fonte_url=_fonte_url(conn, alvo.norma_urn, urls),
                score=max(rerank) if rerank else fusao,
                score_fusao=fusao,
                score_rerank=max(rerank) if rerank else None,
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
    obrigatorios: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Nó de destino mais os descendentes VIGENTES, em ordem de documento.

    Devolve o texto e os IDs que entraram nele. A lista não é derivável do texto depois, e é
    ela que responde, no eval, se o dispositivo esperado chegou de fato ao modelo.

    **`obrigatorios` reserva orçamento para os dispositivos que a busca achou.** Sem isso, o
    art. 5º da CF — 78 incisos, bem mais que `max_chars` — era cortado antes do inciso
    LXXVIII, e a pergunta `lex-01` do golden recuperava o artigo certo com o texto errado:
    recall 0 com o dispositivo em primeiro lugar. O preenchimento do resto segue a ordem de
    documento até estourar o limite.

    O corte é sempre em fronteira de dispositivo: um artigo cortado no meio de uma frase
    chegaria ao modelo como texto legal mutilado, e a citação que saísse dali não bateria
    com a fonte. Se o próprio nó de destino já estoura o limite, ele vai inteiro assim
    mesmo — nunca se corta o texto que a busca escolheu.
    """
    partes = [alvo]
    if descendentes:
        partes += queries.subarvore(conn, alvo.id, data_referencia=data_referencia)
    partes = [p for p in partes if p.texto.strip()]

    reservados = {alvo.id, *obrigatorios}
    escolhidos = {p.id for p in partes if p.id in reservados}
    total = sum(len(linha_dispositivo(p)) + 1 for p in partes if p.id in escolhidos)

    for p in partes:
        if p.id in escolhidos:
            continue
        custo = len(linha_dispositivo(p)) + 1
        if total + custo > max_chars:
            break
        escolhidos.add(p.id)
        total += custo

    linhas: list[str] = []
    dentro: list[str] = []
    omitiu = False
    for p in partes:
        if p.id not in escolhidos:
            omitiu = True
            continue
        if omitiu and linhas:
            linhas.append(MARCA_OMISSAO)
        omitiu = False
        linhas.append(linha_dispositivo(p))
        dentro.append(p.id)
    return "\n".join(linhas), dentro


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
