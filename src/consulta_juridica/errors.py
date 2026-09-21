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
