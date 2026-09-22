"""Golden set: perguntas com o dispositivo correto anotado à mão."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..store import queries
from ..urn import resolver_rotulo


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

    `dispositivos_esperados` guarda o ID canônico; `rotulos_esperados`, o que o humano
    escreveu. Os dois porque o arquivo precisa continuar legível para ser anotado à mão, e
    o relatório precisa ser legível para ser lido.
    """

    id: str
    mecanismo: Mecanismo
    pergunta: str
    data_referencia: date
    dispositivos_esperados: list[str]
    deve_abster: bool = False
    notas: str | None = None
    rotulos_esperados: list[str] = []


def carregar(caminho: Path) -> list[ItemGolden]:
    """Lê o JSONL e resolve os rótulos anotados para IDs canônicos.

    A resolução acontece aqui, e o arquivo continua com "CF/88 art. 5º LXXVIII": anotação à
    mão é o artefato mais caro do eval, e obrigar um humano a escrever URN de 50 caracteres
    garante erro de digitação — que é exatamente o que este carregamento deveria estar
    caçando. Ver `urn.resolver_rotulo`.

    Falha alto, com o número da linha. Um esperado que não resolve derruba o recall em
    silêncio e faz parecer defeito da recuperação.
    """
    itens: list[ItemGolden] = []
    vistos: set[str] = set()
    for n, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            item = ItemGolden.model_validate_json(linha)
        except ValidationError as e:
            raise ValueError(f"{caminho.name}:{n}: {e}") from None

        item.rotulos_esperados = list(item.dispositivos_esperados)
        try:
            item.dispositivos_esperados = [
                resolver_rotulo(r) for r in item.rotulos_esperados
            ]
        except ValueError as e:
            raise ValueError(f"{caminho.name}:{n}: item {item.id!r}: {e}") from None

        if item.id in vistos:
            raise ValueError(f"{caminho.name}:{n}: id repetido: {item.id!r}")
        vistos.add(item.id)
        itens.append(item)
    return itens


def validar(itens: Sequence[ItemGolden], conn: sqlite3.Connection) -> list[str]:
    """Confere se os dispositivos esperados existem no SQLite; devolve os problemas.

    Anotação à mão erra ID, e um esperado inexistente derruba o recall silenciosamente,
    fazendo parecer defeito da recuperação.

    Três checagens, e a terceira é a que não é óbvia:

    1. o dispositivo existe no corpus ingerido;
    2. `deve_abster` e `dispositivos_esperados` não se contradizem;
    3. **o esperado estava em vigor na data de referência do item.** É o inverso do erro que
       o sistema existe para evitar: anotar como resposta certa um dispositivo que não
       vigorava naquela data faz a recuperação parecer quebrada quando ela acertou. Aqui
       vale qualquer redação do dispositivo — o golden anota "art. 6º", e qual redação
       responde é o que a data decide.
    """
    problemas: list[str] = []
    for item in itens:
        if item.deve_abster and item.dispositivos_esperados:
            problemas.append(f"{item.id}: deve_abster com dispositivos esperados")
        if not item.deve_abster and not item.dispositivos_esperados:
            problemas.append(f"{item.id}: sem esperados e sem deve_abster")

        for esperado, rotulo in zip(
            item.dispositivos_esperados, item.rotulos_esperados, strict=True
        ):
            if not queries.redacoes(conn, esperado):
                problemas.append(f"{item.id}: {rotulo!r} -> {esperado} não existe no corpus")
            elif not queries.redacoes(conn, esperado, data_referencia=item.data_referencia):
                problemas.append(
                    f"{item.id}: {rotulo!r} não vigorava em {item.data_referencia}, "
                    f"em nenhuma das suas redações"
                )
    return problemas
