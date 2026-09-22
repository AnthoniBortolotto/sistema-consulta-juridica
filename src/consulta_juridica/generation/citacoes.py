"""Citação bruta -> `Citacao` de domínio.

Duas estratégias para o mesmo resultado, porque os backends diferem: o da Messages API
devolve citations estruturadas, o do CLI devolve âncoras no meio do texto. Converter as
duas para o mesmo tipo é o que impede o vazamento do backend chegar até a API HTTP.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Final

from ..models import Citacao, Trecho
from ..retrieval.expansao import MARCA_OMISSAO
from .backend import BlocoDocumento, CitacaoBruta
from .prompt import MARCA_ABSTENCAO, PREFIXO_REF

#: Âncora que o backend sem citations nativas emite: `[D1]`.
_RE_ANCORA: Final = re.compile(rf"\[({PREFIXO_REF}\d+)\]")


def resolver(
    brutas: Sequence[CitacaoBruta],
    docs: Sequence[BlocoDocumento],
    trechos: Sequence[Trecho],
    rotulo_de: Callable[[str], str] | None = None,
) -> list[Citacao]:
    """Liga cada citação ao dispositivo e à URL de origem.

    Citação que não casar com nenhum documento é descartada, não adivinhada: citação
    inventada é exatamente o defeito que este projeto existe para evitar.

    O trabalho real é o deslocamento de caractere virar dispositivo. A API cita um intervalo
    dentro do texto que enviamos, e esse texto é o trecho expandido — um artigo inteiro, com
    o caput e os incisos em linhas. Qual dispositivo foi citado é quem ocupa aquele intervalo,
    e isso se sabe porque `retrieval.expansao` devolve as linhas na ordem em `dispositivos`.

    Três descartes, cada um um defeito diferente:

    - documento inexistente — o modelo referenciou uma âncora que não demos;
    - texto citado que não bate com o intervalo — sinal de que o corpo enviado divergiu de
      `Trecho.texto`, e então TODOS os deslocamentos estão errados, não só este;
    - intervalo que cai só na marca de omissão — a citação seria de texto que não é lei.

    `rotulo_de` traduz o dispositivo citado para o rótulo humano. Sem ele a citação herda o
    rótulo do TRECHO, e aí o par fica inconsistente: `dispositivo_id` apontando para o
    parágrafo único e o rótulo dizendo "Art. 49". Opcional porque a função é pura de
    propósito — quem tem banco é o serviço, e é ele que passa a tradução.
    """
    por_ref = {d.ref: d for d in docs}
    por_id = {t.dispositivo_id: t for t in trechos}
    resolvidas: list[Citacao] = []

    for bruta in brutas:
        doc = por_ref.get(bruta.ref_documento)
        if doc is None:
            continue
        trecho = por_id.get(doc.dispositivo_id)
        if trecho is None:
            continue

        alvo = trecho.dispositivo_id
        if bruta.inicio_char is not None and bruta.fim_char is not None:
            if not _texto_confere(trecho, bruta):
                continue
            achado = _dispositivo_em(trecho, bruta.inicio_char, bruta.fim_char)
            if achado is None:
                continue
            alvo = achado

        rotulo = rotulo_de(alvo) if rotulo_de else ""
        resolvidas.append(
            Citacao(
                dispositivo_id=alvo,
                rotulo_completo=rotulo or trecho.rotulo_completo,
                texto_citado=bruta.texto_citado,
                fonte_url=trecho.fonte_url,
                inicio_char=bruta.inicio_char,
                fim_char=bruta.fim_char,
            )
        )
    return resolvidas


def _texto_confere(trecho: Trecho, bruta: CitacaoBruta) -> bool:
    """O intervalo citado contém mesmo o texto que a API devolveu?

    Deveria ser sempre verdade — a API extrai `cited_text` do documento que enviamos. É
    justamente por isso que vale conferir: a única forma de falhar é o corpo enviado não
    ser `Trecho.texto`, e aí o defeito não é desta citação, é de todas.
    """
    return trecho.texto[bruta.inicio_char : bruta.fim_char] == bruta.texto_citado


def faixas(trecho: Trecho) -> list[tuple[int, int, str]]:
    """(início, fim, dispositivo_id) de cada linha citável do trecho.

    O texto do trecho é uma linha por dispositivo, na ordem de `dispositivos`, com a marca
    de omissão intercalada onde o corte descartou algo. Percorrer as linhas consumindo a
    lista reconstrói os intervalos sem precisar consultar o banco de novo.
    """
    saida: list[tuple[int, int, str]] = []
    restantes = list(trecho.dispositivos)
    pos = 0
    for linha in trecho.texto.split("\n"):
        fim = pos + len(linha)
        if linha != MARCA_OMISSAO and restantes:
            saida.append((pos, fim, restantes.pop(0)))
        pos = fim + 1  # o "\n"
    return saida


def _dispositivo_em(trecho: Trecho, inicio: int, fim: int) -> str | None:
    """O dispositivo que mais se sobrepõe ao intervalo citado.

    Uma citação pode atravessar a fronteira entre o caput e um inciso — a API divide por
    sentença, e a técnica legislativa faz o caput terminar em dois-pontos com o inciso
    completando a frase. Atribuir ao de maior sobreposição é a escolha que erra menos; sem
    sobreposição nenhuma, não há o que atribuir.
    """
    melhor, maior = None, 0
    for ini_d, fim_d, disp in faixas(trecho):
        sobreposicao = min(fim, fim_d) - max(inicio, ini_d)
        if sobreposicao > maior:
            melhor, maior = disp, sobreposicao
    return melhor


def extrair_ancoras(texto: str) -> list[CitacaoBruta]:
    """Fallback do backend CLI: acha os marcadores de documento no texto gerado.

    Sem texto citado e sem deslocamento: o modelo apontou o trecho, não um intervalo dentro
    dele. A citação resultante aponta para o dispositivo do trecho e nada mais — é a perda
    concreta de não usar a Messages API, e a razão de `suporta_citacoes` ser False ali.
    """
    return [
        CitacaoBruta(ref_documento=ref, texto_citado="")
        for ref in dict.fromkeys(_RE_ANCORA.findall(texto))
    ]


def detectar_abstencao(texto: str) -> bool:
    """Reconhece a recusa explícita combinada no prompt de sistema."""
    return texto.strip().upper().startswith(MARCA_ABSTENCAO)
