"""Critérios de consulta -> filtro do Qdrant.

Módulo pequeno e isolado porque é onde os bugs de vigência vão morar, e porque erro aqui
não levanta exceção: devolve menos resultados, ou resultados errados, em silêncio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ..models import TipoDispositivo

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
    """
    raise NotImplementedError
