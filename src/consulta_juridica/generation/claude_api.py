"""Backend via Messages API, com citations nativas. Caminho de eval e de demonstração."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .backend import LLMBackend, Pedido, ResultadoGeracao

if TYPE_CHECKING:
    from anthropic import Anthropic


class BackendMessagesAPI(LLMBackend):
    """Envia cada trecho como bloco `document` com citations habilitadas.

    É o diferencial do projeto: a resposta volta particionada em blocos de texto, e os
    blocos citados carregam o texto citado e a localização no documento de origem — citação
    verificada pela API, não pedida por prompt e torcida para que o modelo obedeça.

    Ao implementar: confirmar na documentação a forma exata do `source` para conteúdo
    próprio (não-PDF) e o tipo de location correspondente. Não escrever de memória.
    """

    nome = "messages-api"
    suporta_citacoes = True

    def __init__(self, modelo: str, client: Anthropic | None = None) -> None:
        raise NotImplementedError

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        raise NotImplementedError
