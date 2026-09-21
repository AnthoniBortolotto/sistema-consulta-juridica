"""Geração de vetores — denso (bge-m3) e esparso (BM25).

Mora na raiz do pacote, e não em `ingest/`, de propósito: a consulta também precisa
embedar, e `retrieval` importando `ingest` inverteria a dependência. Mais importante,
indexação e consulta PRECISAM usar o mesmo modelo e a mesma tokenização — misturar
tokenizadores gera IDs de termo incompatíveis e recall silenciosamente ruim. Manter
`documentos()` e `consulta()` na mesma tela é o que torna essa divergência visível.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from qdrant_client.models import SparseVector

    from .config import Settings


@dataclass(frozen=True)
class Vetores:
    """Par denso + esparso de um mesmo texto."""

    denso: list[float]
    esparso: SparseVector


class Encoder(Protocol):
    """Contrato de codificação. Implementações carregam modelos pesados — reutilize."""

    nome_denso: str
    nome_esparso: str
    dim: int

    def documentos(self, textos: Sequence[str], *, lote: int = 32) -> Iterator[Vetores]:
        """Codifica textos para indexação."""
        ...

    def consulta(self, texto: str) -> Vetores:
        """Codifica a pergunta do usuário."""
        ...


class EncoderLocal(Encoder):
    """bge-m3 via sentence-transformers (denso) + Qdrant/bm25 via fastembed (esparso).

    O BM25 é instanciado com `language="portuguese"`: com o stemmer inglês, o recall em
    texto jurídico brasileiro cai bastante.
    """

    def __init__(
        self,
        modelo_denso: str,
        modelo_esparso: str,
        *,
        idioma: str = "portuguese",
    ) -> None:
        raise NotImplementedError


def construir_encoder(cfg: Settings) -> Encoder:
    """Fábrica. Chamada uma única vez, pelo composition root."""
    raise NotImplementedError
