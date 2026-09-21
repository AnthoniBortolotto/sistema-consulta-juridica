"""Backend via `claude -p`, usando a assinatura do usuário. Caminho de desenvolvimento."""

from __future__ import annotations

from .backend import LLMBackend, Pedido, ResultadoGeracao


class BackendClaudeCLI(LLMBackend):
    """Chama o Claude Code em modo não-interativo por subprocess.

    Existe para iterar sem gastar API. Duas limitações reais, não contornáveis:

    1. Não é a Messages API, portanto NÃO há citations nativas. As citações vêm de âncoras
       que o prompt pede e `citacoes.extrair_ancoras` parseia. É mais frágil.
    2. O uso consome os limites normais da assinatura, os mesmos usados para programar.
       Rodar um eval inteiro por aqui pode deixar o usuário rate-limited.

    Portanto: desenvolvimento sim, números de eval não.
    """

    nome = "claude-cli"
    suporta_citacoes = False

    def __init__(self, executavel: str = "claude", timeout_s: int = 180) -> None:
        raise NotImplementedError

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        raise NotImplementedError
