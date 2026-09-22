"""Critérios de consulta -> filtro do Qdrant.

Módulo pequeno e isolado porque é onde os bugs de vigência vão morar, e porque erro aqui
não levanta exceção: devolve menos resultados, ou resultados errados, em silêncio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ..models import TipoDispositivo
from ..vectorstore import Campo, dia

if TYPE_CHECKING:
    from qdrant_client import models as qmodels


@dataclass(frozen=True)
class Criterios:
    """Recorte da consulta. Atravessa a pipeline inteira, até a expansão.

    `data_referencia` NÃO tem default, deliberadamente: um default viraria `date.today()`
    em algum ponto, e a consulta sobre o direito vigente à época de um fato deixaria de ser
    possível sem ninguém perceber.
    """

    data_referencia: date
    normas: tuple[str, ...] = ()
    tipos: tuple[TipoDispositivo, ...] = ()
    incluir_revogados: bool = False


def construir_filtro(c: Criterios) -> qmodels.Filter:
    """Monta o filtro.

    Vigência vira comparação entre inteiros ordinais:
    `vigencia_inicio_dia <= ref` e `revogado_em_dia > ref`, onde o não revogado carrega
    `SENTINELA_VIGENTE`. Ver `vectorstore` para o porquê da sentinela.

    `gt` e não `gte`: a revogação produz efeito NA data, então o dispositivo já não vale
    nesse dia. **Tem de ser idêntico a `store.queries.PREDICADO_VIGENTE`** — se
    divergirem, índice e expansão discordam sobre o que está em vigor, sem exceção e sem
    log. `tests/test_filtros.py` compara os dois lados contra o mesmo corpus.

    `incluir_revogados` solta SÓ a condição de revogação, não a de início de vigência:
    quem consulta o histórico quer o que já esteve em vigor até a data, não o que ainda
    vai entrar. Texto em vacatio legis nunca é direito aplicável a data nenhuma do
    passado.
    """
    from qdrant_client import models

    ref = dia(c.data_referencia)
    must: list[models.Condition] = [
        models.FieldCondition(key=Campo.VIGENCIA_INICIO.value, range=models.Range(lte=ref))
    ]
    if not c.incluir_revogados:
        must.append(
            models.FieldCondition(key=Campo.REVOGADO_EM.value, range=models.Range(gt=ref))
        )
    if c.normas:
        must.append(
            models.FieldCondition(
                key=Campo.NORMA_URN.value, match=models.MatchAny(any=list(c.normas))
            )
        )
    if c.tipos:
        must.append(
            models.FieldCondition(
                key=Campo.TIPO.value, match=models.MatchAny(any=[t.value for t in c.tipos])
            )
        )
    return models.Filter(must=must)
