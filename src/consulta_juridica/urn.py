"""Identificadores LexML.

Usado por parser, filtros, expansão e API. Sem um módulo próprio, a manipulação dessas
strings vira tratamento ad hoc espalhado por quatro lugares.

Formato: urn:lex:br:federal:lei:1990-09-11;8078!art6_inc8
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class UrnPartes:
    """Decomposição de uma URN LexML."""

    esfera: str
    tipo: str
    data: date | None
    numero: str
    fragmento: str | None  # "art6_inc8", quando aponta para um dispositivo


def montar_urn_norma(tipo: str, numero: str, data: date, esfera: str = "federal") -> str:
    """Compõe a URN de uma norma inteira."""
    raise NotImplementedError


def montar_urn_dispositivo(norma_urn: str, caminho: str) -> str:
    """Anexa o fragmento de dispositivo à URN da norma."""
    raise NotImplementedError


def parse_urn(urn: str) -> UrnPartes:
    """Decompõe uma URN. Levanta ValueError se malformada."""
    raise NotImplementedError


def resolver_apelido(texto: str) -> str | None:
    """Traduz apelido corrente para URN: "CDC" -> urn:lex:...;8078. None se desconhecido."""
    raise NotImplementedError


def rotulo_humano(urn: str) -> str:
    """URN -> "Lei 8.078/1990"."""
    raise NotImplementedError


def url_planalto(norma_urn: str) -> str:
    """URL pública da norma, para o usuário conferir a citação na fonte oficial."""
    raise NotImplementedError
