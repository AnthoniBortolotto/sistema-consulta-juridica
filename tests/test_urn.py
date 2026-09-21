"""Esquema de ID canônico.

Um ID errado não levanta exceção: produz dispositivo órfão no índice, e o sintoma aparece
semanas depois como recall baixo. Por isso os casos de erro são testados tanto quanto os
de sucesso.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from consulta_juridica.models import TipoDispositivo as T
from consulta_juridica.urn import (
    fragmento_de_caminho,
    montar_urn_dispositivo,
    montar_urn_norma,
    parse_segmento,
    parse_urn,
    resolver_apelido,
    resolver_rotulo,
    romano_para_int,
    rotulo_humano,
    segmento,
    url_planalto,
)

CF = "urn:lex:br:federal:constituicao:1988-10-05;1988"
CDC = "urn:lex:br:federal:lei:1990-09-11;8078"
CC = "urn:lex:br:federal:lei:2002-01-10;10406"


# --- numerais -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("I", 1), ("IV", 4), ("VIII", 8), ("XXVI", 26), ("LXVII", 67), ("LXXVIII", 78)],
)
def test_romano_para_int(texto, esperado):
    assert romano_para_int(texto) == esperado


@pytest.mark.parametrize("texto", ["IIII", "VV", "IC", "", "8", "abc"])
def test_romano_malformado_levanta(texto):
    """"IIII" somaria 4 e passaria despercebido. Tem de explodir."""
    with pytest.raises(ValueError):
        romano_para_int(texto)


# --- segmentos ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "rotulo", "esperado"),
    [
        (T.ARTIGO, "Art. 6º", "art6"),
        (T.ARTIGO, "Art. 103-A", "art103a"),  # 45 ocorrências na CF
        (T.ARTIGO, "Artigo 5", "art5"),
        (T.CAPUT, "", "cpt"),
        (T.PARAGRAFO, "§ 1º", "par1"),
        (T.PARAGRAFO, "§ 2º-A", "par2a"),  # 51 ocorrências na CF
        (T.PARAGRAFO, "Parágrafo único", "parunico"),  # 117 CF, 33 CDC, 380 CC
        (T.INCISO, "LXXVIII", "inc78"),
        (T.INCISO, "VIII", "inc8"),
        (T.ALINEA, "a)", "ali1"),
        (T.ALINEA, "c", "ali3"),
        (T.ITEM, "2", "ite2"),
        # estruturais também vêm em romano na fonte
        (T.TITULO, "Título II", "tit2"),
        (T.CAPITULO, "Capítulo III", "cap3"),
        (T.SECAO, "Seção IV", "sec4"),
        (T.SUBSECAO, "Subseção I", "sub1"),
        (T.LIVRO, "Livro III", "liv3"),
        (T.PARTE, "Parte I", "prt1"),  # "par" antes de "parte" no prefixo daria "te i"
    ],
)
def test_segmento(tipo, rotulo, esperado):
    assert segmento(tipo, rotulo) == esperado


def test_segmento_recusa_rotulo_desconhecido():
    with pytest.raises(ValueError):
        segmento(T.ARTIGO, "Art. quinto")


@pytest.mark.parametrize(
    ("seg", "esperado"),
    [
        ("art6", (T.ARTIGO, 6, "")),
        ("art103a", (T.ARTIGO, 103, "a")),
        ("inc78", (T.INCISO, 78, "")),
        ("cpt", (T.CAPUT, None, "")),
        ("parunico", (T.PARAGRAFO, None, "")),
    ],
)
def test_parse_segmento(seg, esperado):
    assert parse_segmento(seg) == esperado


# --- fragmento ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caminho", "esperado"),
    [
        # regra 1: ancestral estrutural não entra no ID de um artigo
        ("tit2/cap1/art5/inc78", "art5_inc78"),
        ("prt1/liv3/tit2/cap1/sec2/art927", "art927"),
        ("art6/cpt", "art6_cpt"),
        # nó estrutural não tem artigo acima: caminho inteiro
        ("tit2/cap1", "tit2_cap1"),
        # regra 2: o componente sobrevive ao corte
        ("adct/art5", "adct_art5"),
        ("adct/art5/par1", "adct_art5_par1"),
    ],
)
def test_fragmento_de_caminho(caminho, esperado):
    assert fragmento_de_caminho(caminho) == esperado


def test_adct_nao_colide_com_o_corpo_permanente():
    """134 números de artigo existem nos dois espaços da CF. Sem o prefixo, o PK colide."""
    assert montar_urn_dispositivo(CF, "tit2/cap1/art5") != montar_urn_dispositivo(CF, "adct/art5")


def test_montar_urn_dispositivo_recusa_urn_ja_com_fragmento():
    with pytest.raises(ValueError):
        montar_urn_dispositivo(f"{CDC}!art6", "art7")


# --- URNs -----------------------------------------------------------------------------


def test_montar_urn_norma():
    assert montar_urn_norma("lei", "8078", date(1990, 9, 11)) == CDC


def test_parse_urn_ida_e_volta():
    p = parse_urn(f"{CDC}!art6_inc8")
    assert (p.esfera, p.tipo, p.numero, p.fragmento) == ("federal", "lei", "8078", "art6_inc8")
    assert p.data == date(1990, 9, 11)


@pytest.mark.parametrize(
    "urn", ["", "urn:lex:br:federal:lei", "lei 8078", "urn:lex:us:federal:law:2020-01-01;1"]
)
def test_parse_urn_malformada(urn):
    with pytest.raises(ValueError):
        parse_urn(urn)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("CDC", CDC), ("cdc", CDC), ("Código de Defesa do Consumidor", CDC),
     ("CF/88", CF), ("Constituição", CF), ("CC", CC), ("Código Civil", CC), ("CLT", None)],
)
def test_resolver_apelido(texto, esperado):
    assert resolver_apelido(texto) == esperado


def test_rotulo_humano():
    assert rotulo_humano(f"{CDC}!art6_inc8") == "Lei 8.078/1990"
    assert rotulo_humano(CF) == "Constituição Federal de 1988"


def test_url_planalto_conferida_em_2026_09_21():
    assert url_planalto(CDC) == "https://www.planalto.gov.br/ccivil_03/leis/l8078.htm"
    with pytest.raises(ValueError):
        url_planalto("urn:lex:br:federal:lei:1973-01-11;5869")


# --- rótulo humano -> ID --------------------------------------------------------------


@pytest.mark.parametrize(
    ("rotulo", "esperado"),
    [
        ("CF/88 art. 5º LXXVIII", f"{CF}!art5_inc78"),
        ("CF/88 art. 5º LXVII", f"{CF}!art5_inc67"),
        ("CF/88 art. 37 § 6º", f"{CF}!art37_par6"),
        ("CF/88 art. 6º", f"{CF}!art6"),
        ("CDC art. 49", f"{CDC}!art49"),
        ("CDC art. 27", f"{CDC}!art27"),
        ("CDC art. 6º VIII", f"{CDC}!art6_inc8"),
        ("CC art. 927", f"{CC}!art927"),
        ("CC art. 205", f"{CC}!art205"),
        ("CC art. 206 § 3º V", f"{CC}!art206_par3_inc5"),
        ("CF/88 ADCT art. 5º", f"{CF}!adct_art5"),
        ("CF/88 art. 103-A", f"{CF}!art103a"),
        ("CDC art. 54 parágrafo único", f"{CDC}!art54_parunico"),
        ("CF/88 art. 7º XXVI a", f"{CF}!art7_inc26_ali1"),
    ],
)
def test_resolver_rotulo(rotulo, esperado):
    assert resolver_rotulo(rotulo) == esperado


@pytest.mark.parametrize(
    "rotulo", ["CPC art. 1009", "CDC", "art. 5º", "CDC artigo quinto"]
)
def test_resolver_rotulo_recusa_em_vez_de_chutar(rotulo):
    """Esperado errado derruba o recall em silêncio e parece defeito da recuperação."""
    with pytest.raises(ValueError):
        resolver_rotulo(rotulo)


def test_golden_set_inteiro_resolve():
    """Os 15 dispositivos anotados à mão precisam virar ID canônico sem exceção.

    É este teste que fecha o item da fase 1: o esquema de ID não serve se o golden set,
    que é o artefato manual mais caro do projeto, não couber nele.
    """
    caminho = Path(__file__).parents[1] / "golden" / "seed.jsonl"
    rotulos = [
        r
        for linha in caminho.read_text(encoding="utf-8").splitlines()
        if linha.strip()
        for r in json.loads(linha)["dispositivos_esperados"]
    ]
    assert rotulos, "golden/seed.jsonl sem dispositivos esperados"
    for r in rotulos:
        assert resolver_rotulo(r).startswith("urn:lex:br:federal:"), r
