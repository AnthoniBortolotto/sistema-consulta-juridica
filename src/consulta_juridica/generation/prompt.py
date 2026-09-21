"""Montagem do prompt. Único lugar onde texto de instrução existe.

Instruções espalhadas pelo código impedem comparar duas rodadas de eval: `VERSAO_PROMPT`
entra em `Resposta` e no relatório justamente para tornar a comparação possível.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final

from ..models import Trecho
from .backend import BlocoDocumento, Pedido

VERSAO_PROMPT: Final = "v1"


def sistema(data_referencia: date, *, exigir_ancoras: bool) -> str:
    """Prompt de sistema.

    Precisa impor três coisas: responder apenas a partir dos trechos fornecidos, abster-se
    explicitamente quando eles não bastarem, e tratar `data_referencia` como a data de
    vigência da consulta. `exigir_ancoras` liga a instrução de marcar as referências no
    texto, necessária apenas no backend sem citations nativas.
    """
    raise NotImplementedError


def montar_documentos(trechos: Sequence[Trecho]) -> list[BlocoDocumento]:
    """Trechos recuperados -> blocos de documento, com refs estáveis D1..Dn."""
    raise NotImplementedError


def montar_pedido(
    pergunta: str,
    trechos: Sequence[Trecho],
    data_referencia: date,
    *,
    suporta_citacoes: bool,
) -> Pedido:
    """Monta o pedido completo."""
    raise NotImplementedError


def render_texto_unico(pedido: Pedido) -> str:
    """Lineariza o pedido em uma string só, para o backend CLI, que não aceita blocos."""
    raise NotImplementedError
