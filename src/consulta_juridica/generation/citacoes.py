"""Citação bruta -> `Citacao` de domínio.

Duas estratégias para o mesmo resultado, porque os backends diferem: o da Messages API
devolve citations estruturadas, o do CLI devolve âncoras no meio do texto. Converter as
duas para o mesmo tipo é o que impede o vazamento do backend chegar até a API HTTP.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Citacao, Trecho
from .backend import BlocoDocumento, CitacaoBruta


def resolver(
    brutas: Sequence[CitacaoBruta],
    docs: Sequence[BlocoDocumento],
    trechos: Sequence[Trecho],
) -> list[Citacao]:
    """Liga cada citação ao dispositivo e à URL de origem.

    Citação que não casar com nenhum documento é descartada, não adivinhada: citação
    inventada é exatamente o defeito que este projeto existe para evitar.
    """
    raise NotImplementedError


def extrair_ancoras(texto: str) -> list[CitacaoBruta]:
    """Fallback do backend CLI: acha os marcadores de documento no texto gerado."""
    raise NotImplementedError


def detectar_abstencao(texto: str) -> bool:
    """Reconhece a recusa explícita combinada no prompt de sistema."""
    raise NotImplementedError
