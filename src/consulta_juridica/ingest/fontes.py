"""Aquisição dos documentos brutos (Planalto, LexML).

Separado do parser de propósito: baixar é lento e sujeito a rede, parsear é rápido e
determinístico. Guardar o bruto em disco permite reparsear o corpus inteiro sem baixar
nada de novo — e reparsear é o que mais acontece enquanto o parser amadurece.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ReferenciaNorma:
    """Ponteiro para uma norma antes do download."""

    urn: str
    url: str
    apelido: str | None = None


@dataclass(frozen=True)
class DocumentoBruto:
    """Bytes como vieram da fonte, com procedência."""

    urn: str
    url: str
    conteudo: bytes
    content_type: str
    sha256: str
    baixado_em: datetime


class Fonte(Protocol):
    """Origem de documentos legais."""

    nome: str

    def listar(self) -> Iterator[ReferenciaNorma]:
        """Normas que esta fonte sabe entregar."""
        ...

    def baixar(self, ref: ReferenciaNorma) -> DocumentoBruto:
        """Baixa uma norma."""
        ...


class FontePlanalto(Fonte):
    """HTML do planalto.gov.br."""

    nome = "planalto"

    def __init__(self, refs: list[ReferenciaNorma]) -> None:
        raise NotImplementedError


class FonteLexML(Fonte):
    """XML via API SRU do LexML."""

    nome = "lexml"

    def __init__(self, base_url: str = "https://www.lexml.gov.br/busca/SRU") -> None:
        raise NotImplementedError


def salvar(doc: DocumentoBruto, destino: Path) -> Path:
    """Grava o bruto em `data/raw/`, junto de um .meta.json com a procedência."""
    raise NotImplementedError


def carregar(caminho: Path) -> DocumentoBruto:
    """Relê um bruto salvo, para reparsear sem rede."""
    raise NotImplementedError
