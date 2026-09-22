"""Backend via `claude -p`, usando a assinatura do usuário. Caminho de desenvolvimento."""

from __future__ import annotations

import subprocess
from typing import Final

from ..models import Uso
from .backend import LLMBackend, Pedido, ResultadoGeracao
from .citacoes import extrair_ancoras
from .prompt import render_texto_unico

#: Modelo declarado no resultado. O CLI escolhe o modelo por conta própria e não devolve
#: qual usou, então gravar um ID específico seria inventar procedência — e `Resposta.modelo`
#: entra no relatório de eval justamente para duas rodadas serem comparáveis.
MODELO_DESCONHECIDO: Final = "claude-cli:indeterminado"


class BackendClaudeCLI(LLMBackend):
    """Chama o Claude Code em modo não-interativo por subprocess.

    Existe para iterar sem gastar API. Duas limitações reais, não contornáveis:

    1. Não é a Messages API, portanto NÃO há citations nativas. As citações vêm de âncoras
       que o prompt pede e `citacoes.extrair_ancoras` parseia. É mais frágil.
    2. O uso consome os limites normais da assinatura, os mesmos usados para programar.
       Rodar um eval inteiro por aqui pode deixar o usuário rate-limited.

    Portanto: desenvolvimento sim, números de eval não.

    Uma terceira limitação apareceu ao implementar: o CLI não reporta tokens, então `uso`
    volta zerado. Não é descuido — é o mesmo motivo de `suporta_citacoes` ser False. Quem
    precisa de número usa a Messages API.
    """

    nome = "claude-cli"
    suporta_citacoes = False

    def __init__(self, executavel: str = "claude", timeout_s: int = 180) -> None:
        self.executavel = executavel
        self.timeout_s = timeout_s

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        from ..errors import BackendIndisponivel

        try:
            processo = subprocess.run(  # noqa: S603 - executável vem da configuração
                [self.executavel, "-p", render_texto_unico(pedido)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                # Um byte fora do utf-8 na saída derrubaria a chamada inteira com
                # UnicodeDecodeError. Aqui, diferente da Messages API, não há citação
                # literal a preservar — as citações são âncoras `[D1]`, e perder a
                # resposta toda por um caractere é troca ruim.
                errors="replace",
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError as e:
            raise BackendIndisponivel(
                f"executável {self.executavel!r} não encontrado; instale o Claude Code ou "
                f"use CJ_BACKEND_LLM=api"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise BackendIndisponivel(
                f"{self.executavel} não respondeu em {self.timeout_s}s"
            ) from e

        if processo.returncode != 0:
            raise BackendIndisponivel(
                f"{self.executavel} saiu com {processo.returncode}: "
                f"{(processo.stderr or '').strip()[:400]}"
            )

        texto = (processo.stdout or "").strip()
        return ResultadoGeracao(
            texto=texto,
            citacoes=extrair_ancoras(texto),
            modelo=MODELO_DESCONHECIDO,
            uso=Uso(),
        )
