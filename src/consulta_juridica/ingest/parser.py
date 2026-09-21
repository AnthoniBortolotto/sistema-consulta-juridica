"""Documento bruto -> árvore de dispositivos.

Responsabilidade única: FIDELIDADE À FONTE. O parser reconstrói a hierarquia tal como
publicada e não decide granularidade de recuperação — isso é `chunking.py`. Misturar as
duas obriga a reparsear todo o corpus a cada experimento de chunk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models import Dispositivo, Norma, Remissao
from .fontes import DocumentoBruto


@dataclass
class NormaParseada:
    """Resultado do parse de um documento."""

    norma: Norma
    #: ordem de documento, pais sempre antes dos filhos
    dispositivos: list[Dispositivo]
    remissoes: list[Remissao]
    #: estruturas não reconhecidas; alimenta o refino do parser
    avisos: list[str] = field(default_factory=list)


class Parser(Protocol):
    def suporta(self, doc: DocumentoBruto) -> bool: ...

    def parse(self, doc: DocumentoBruto) -> NormaParseada: ...


class ParserPlanaltoHTML(Parser):
    """HTML do Planalto. Marcação irregular — a hierarquia vem de heurística sobre rótulos."""


class ParserLexMLXML(Parser):
    """XML do LexML. Hierarquia explícita no schema; caminho preferencial quando disponível."""


def escolher_parser(doc: DocumentoBruto) -> Parser:
    """Primeiro parser registrado que suporta o documento.

    Levanta `ParserIndisponivel` se nenhum souber tratá-lo.
    """
    raise NotImplementedError


def extrair_remissoes(texto: str, origem: Dispositivo) -> list[Remissao]:
    """Acha referências cruzadas no texto ("na forma do art. 37, § 6º")."""
    raise NotImplementedError
