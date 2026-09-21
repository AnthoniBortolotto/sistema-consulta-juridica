"""Cache em disco das respostas do modelo."""

from __future__ import annotations

from pathlib import Path

from .backend import LLMBackend, Pedido, ResultadoGeracao


class BackendComCache(LLMBackend):
    """Decorator sobre qualquer `LLMBackend`, indexado pela chave do `Pedido`.

    Decorator, e não módulo de cache solto, para compor com os dois backends e com o eval
    sem duplicação. É o maior ganho de custo do desenvolvimento: ao mexer só na recuperação,
    apenas as consultas cujo contexto mudou são efetivamente re-cobradas.
    """

    def __init__(self, interno: LLMBackend, dir_cache: Path) -> None:
        self.interno = interno
        self.dir_cache = dir_cache
        self.nome = f"{interno.nome}+cache"
        self.suporta_citacoes = interno.suporta_citacoes

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        raise NotImplementedError
