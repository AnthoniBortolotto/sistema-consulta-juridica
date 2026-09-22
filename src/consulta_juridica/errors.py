"""Exceções de domínio."""

from __future__ import annotations


class ConsultaJuridicaError(Exception):
    """Base de todas as exceções do projeto."""


class ColecaoIncompativel(ConsultaJuridicaError):
    """A coleção no Qdrant foi indexada com outro modelo ou outra dimensão.

    Levantada por `vectorstore.garantir_colecao`. Continuar apesar dessa divergência
    produz recuperação silenciosamente degradada — por isso é erro, não aviso.
    """


class NormaNaoEncontrada(ConsultaJuridicaError):
    """URN de norma ausente do SQLite."""


class DispositivoNaoEncontrado(ConsultaJuridicaError):
    """ID de dispositivo ausente do SQLite."""


class ParserIndisponivel(ConsultaJuridicaError):
    """Nenhum parser registrado sabe tratar o documento bruto."""


class RecusaDoModelo(ConsultaJuridicaError):
    """O modelo recusou a requisição (`stop_reason: "refusal"`).

    É erro e não abstenção: abstenção é o sistema dizendo que os trechos não bastam, e
    aparece no eval como acerto quando a pergunta pedia isso. Recusa é o modelo não
    respondendo — num corpus de legislação, sinal de defeito, e tem de ser visível.
    """


class BackendIndisponivel(ConsultaJuridicaError):
    """O backend de geração não pôde ser executado (binário ausente, timeout, saída != 0)."""
