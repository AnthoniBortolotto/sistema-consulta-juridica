"""Busca híbrida no Qdrant.

Não há módulo de fusão neste projeto: o RRF roda server-side, via `query_points` com dois
`prefetch` (denso e esparso) e `FusionQuery`. Reimplementar a fusão no cliente só
adicionaria uma segunda fonte de verdade para o ranking.

Atenção: o `query_filter` NÃO desce automaticamente para os prefetch. O filtro precisa ser
repetido dentro de cada um, senão a vigência é aplicada só depois da fusão — sobre uma
lista de candidatos que já veio contaminada.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from ..embedding import Encoder
from ..models import ChunkPayload
from ..store import queries
from ..vectorstore import VETOR_DENSO, VETOR_ESPARSO
from .filtros import Criterios, construir_filtro

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


@dataclass
class Candidato:
    """Resultado cru da busca, antes do rerank e da expansão.

    `texto` nasce vazio: o payload do Qdrant não carrega o texto de propósito (o SQLite é
    a fonte da verdade e duplicá-lo criaria duas cópias para divergir). Quem o preenche é
    `hidratar`, antes do rerank.
    """

    chunk_id: UUID
    dispositivo_id: str
    texto: str
    payload: ChunkPayload
    score: float
    score_rerank: float | None = None


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
    from qdrant_client import models

    filtro = construir_filtro(criterios)
    vetores = encoder.consulta(consulta)

    resposta = client.query_points(
        colecao,
        prefetch=[
            # O filtro vai DENTRO de cada prefetch. Aplicado só no `query_filter`, ele
            # agiria depois da fusão: cada perna gastaria seus `prefetch` lugares com
            # dispositivos revogados e a lista fundida chegaria aqui já empobrecida.
            models.Prefetch(
                query=vetores.denso, using=VETOR_DENSO, filter=filtro, limit=prefetch
            ),
            models.Prefetch(
                query=vetores.esparso, using=VETOR_ESPARSO, filter=filtro, limit=prefetch
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        # Repetido também aqui: redundante quando os prefetch filtram, e é justamente por
        # isso que fica — uma perna que deixe de filtrar não vaza texto revogado.
        query_filter=filtro,
        limit=k,
        with_payload=True,
    )
    return _desempatar([_para_candidato(p) for p in resposta.points])


def _desempatar(cands: list[Candidato]) -> list[Candidato]:
    """Ordem estável entre candidatos com a MESMA pontuação de fusão.

    O RRF soma recíprocos de posição, então empate exato é comum: na pergunta `sem-01` do
    golden, o art. 37 da CF e o art. 43 do CC saem os dois com 0,83333. Qual deles vem
    primeiro é decisão do servidor, e ela varia entre execuções — medido, o MRR do eval
    oscilava entre 0,758 e 0,848 na mesma configuração, o que torna impossível atribuir uma
    diferença a uma mudança de código.

    O desempate por ID é arbitrário e é essa a questão: entre dois candidatos empatados não
    há informação para preferir nenhum, e o que não pode acontecer é a escolha mudar
    sozinha. Só a ordem dentro do empate muda; o ranking não.
    """
    return sorted(cands, key=lambda c: (-c.score, c.dispositivo_id))


def _para_candidato(ponto) -> Candidato:
    return Candidato(
        chunk_id=UUID(str(ponto.id)),
        dispositivo_id=str((ponto.payload or {})["dispositivo_id"]),
        texto="",
        payload=ChunkPayload.model_validate(ponto.payload),
        score=float(ponto.score),
    )


def hidratar(conn: sqlite3.Connection, cands: Sequence[Candidato]) -> list[Candidato]:
    """Preenche `texto` a partir do SQLite, em uma consulta só.

    Descarta o candidato cujo dispositivo não existe mais no banco. Isso acontece quando o
    índice está mais velho que o corpus — e o índice é derivado e reconstruível, então o
    conserto é reindexar. Devolver um trecho sem texto seria pior: chegaria ao modelo como
    citação vazia.
    """
    encontrados = queries.obter_dispositivos(conn, [c.dispositivo_id for c in cands])
    vivos = []
    for c in cands:
        d = encontrados.get(c.dispositivo_id)
        if d is None:
            continue
        c.texto = d.texto
        vivos.append(c)
    return vivos
