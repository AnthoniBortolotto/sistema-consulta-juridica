"""Golden set: perguntas com o dispositivo correto anotado à mão."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class Mecanismo(StrEnum):
    """Qual peça do pipeline a pergunta exercita.

    Existe para o relatório poder quebrar o recall por mecanismo. Um número agregado não
    diz se o problema está na perna léxica, na densa ou na expansão — e são correções
    completamente diferentes.
    """

    LEXICAL = "lexical"
    SEMANTICO = "semantico"
    HIBRIDO = "hibrido"
    VIGENCIA = "vigencia"
    EXPANSAO = "expansao"
    DESAMBIGUACAO = "desambiguacao"
    CRUZAMENTO = "cruzamento"
    ABSTENCAO = "abstencao"
    LIMITE = "limite"


class ItemGolden(BaseModel):
    """Uma pergunta anotada.

    `deve_abster=True` marca os casos onde a resposta certa é "não encontrei". Sem eles, o
    eval premia um sistema que sempre responde — que é pior que um que admite lacuna.
    """

    id: str
    mecanismo: Mecanismo
    pergunta: str
    data_referencia: date
    dispositivos_esperados: list[str]
    deve_abster: bool = False
    notas: str | None = None


def carregar(caminho: Path) -> list[ItemGolden]:
    raise NotImplementedError


def validar(itens: Sequence[ItemGolden], conn: sqlite3.Connection) -> list[str]:
    """Confere se os dispositivos esperados existem no SQLite; devolve os problemas.

    Anotação à mão erra ID, e um esperado inexistente derruba o recall silenciosamente,
    fazendo parecer defeito da recuperação.
    """
    raise NotImplementedError
