"""Árvore de dispositivos -> chunks indexáveis.

Módulo próprio porque esta é a decisão mais consequente do sistema e a que mais vai ser
trocada. Granularidade e quanto contexto hierárquico entra no texto embedado determinam a
qualidade da recuperação, e a estratégia escolhida define o que `retrieval.expansao`
precisa reconstruir depois.

Jamais um splitter por contagem de caracteres: cortar no meio de um artigo separa o inciso
do caput que lhe dá sentido.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol

from ..models import Chunk, Dispositivo, Norma


class Estrategia(Protocol):
    """Como transformar dispositivos em chunks. O nome vai para o relatório de eval."""

    nome: str

    def chunks(self, disps: Sequence[Dispositivo], norma: Norma) -> Iterator[Chunk]: ...


class ChunkPorFolha(Estrategia):
    """Cada folha (inciso, alínea, parágrafo) vira um chunk.

    Recuperação mais precisa, mas depende de `expansao` para devolver o artigo inteiro ao
    modelo — inciso isolado costuma ser ininteligível.
    """

    nome = "folha"


class ChunkPorArtigo(Estrategia):
    """Artigo e seus filhos viram um chunk único.

    Autocontido, dispensa expansão; perde precisão em artigos longos, onde o inciso
    relevante fica diluído no vetor do artigo inteiro.
    """

    nome = "artigo"


def texto_contextualizado(
    d: Dispositivo, ancestrais: Sequence[Dispositivo], norma: Norma
) -> str:
    """Monta o `texto_indexado`: prefixa o texto próprio com a hierarquia.

    Ex.: "Lei 8.078/1990 > Título I > Cap. III > Art. 6º > VIII - <texto>". É o que permite
    à busca densa distinguir dois incisos de redação parecida em capítulos diferentes.
    """
    raise NotImplementedError


def obter_estrategia(nome: str) -> Estrategia:
    """Resolve a estratégia pelo nome vindo de `Settings`."""
    raise NotImplementedError
