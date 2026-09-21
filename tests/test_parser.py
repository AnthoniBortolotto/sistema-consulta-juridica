"""Parser estrutural, contra HTML real do Planalto em `fixtures/`.

Fixture real, não sintética: a marcação do Planalto é irregular, e um parser que só passa
em HTML limpo não sobrevive ao primeiro código de verdade. Onde a fixture não cobre a
estrutura (ela é um recorte de um artigo só), o teste usa HTML sintético no mesmo formato
do Planalto — `<p>` por dispositivo, rótulo abrindo o bloco.
"""

from __future__ import annotations

from datetime import date

import pytest

from consulta_juridica.ingest.parser import (
    ParserPlanaltoHTML,
    blocos_de,
    classificar,
    extrair_remissoes,
)
from consulta_juridica.models import TipoDispositivo as T

from .conftest import URN_CF, doc_html, html_planalto

PARSER = ParserPlanaltoHTML()


def por_caminho(r, caminho: str):
    return next((d for d in r.dispositivos if d.caminho == caminho), None)


# --- classificação de rótulo ----------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "tipo", "segmento"),
    [
        ("Art. 5º Todos são iguais perante a lei", T.ARTIGO, "art5"),
        ("Art. 6o São direitos sociais", T.ARTIGO, "art6"),  # ordinal vinha de <sup>o</sup>
        ("Art. 2.046. Todas as remissões", T.ARTIGO, "art2046"),  # separador de milhar
        ("Art. 103-A. O Supremo Tribunal Federal", T.ARTIGO, "art103a"),
        ("§ 1º A soberania popular", T.PARAGRAFO, "par1"),
        ("§ 2º-A O disposto", T.PARAGRAFO, "par2a"),
        ("Parágrafo único. Todo brasileiro", T.PARAGRAFO, "parunico"),
        ("VIII - a facilitação da defesa", T.INCISO, "inc8"),
        ("LXXVIII - a todos é assegurada", T.INCISO, "inc78"),
        ("a) o salário mínimo", T.ALINEA, "ali1"),
        ("TÍTULO II Dos Direitos e Garantias", T.TITULO, "tit2"),
        ("CAPÍTULO III Dos Direitos Básicos", T.CAPITULO, "cap3"),
        ("LIVRO COMPLEMENTAR Das Disposições Finais", T.LIVRO, "liv99"),
    ],
)
def test_classificar(texto, tipo, segmento):
    r = classificar(texto)
    assert r is not None, texto
    assert (r.tipo, r.segmento) == (tipo, segmento)


@pytest.mark.parametrize(
    ("texto", "rotulo"),
    [
        ("Art. 5º Todos são iguais", "Art. 5º"),
        ("Art. 6o São direitos", "Art. 6º"),  # <sup>o</sup> achatado
        ("Art. 927. Aquele que", "Art. 927"),  # cardinal a partir do 10º
        ("Art. 2.046. Todas as remissões", "Art. 2.046"),  # separador de milhar
        ("Art. 103-A. O Supremo", "Art. 103-A"),
        ("§ 1º A soberania", "§ 1º"),
        ("§ 10. O disposto", "§ 10"),
    ],
)
def test_rotulo_segue_a_tecnica_legislativa(texto, rotulo):
    """O rótulo é o que o usuário confere na fonte: "Art. 927", não "Art. 927.º"."""
    assert classificar(texto).rotulo == rotulo


@pytest.mark.parametrize(
    "texto",
    [
        "na forma do art. 37, § 6º, da Constituição",
        "O PRESIDENTE DA REPÚBLICA, faço saber que o Congresso Nacional decreta",
        "Dispõe sobre a proteção do consumidor.",
    ],
)
def test_prosa_nao_vira_dispositivo(texto):
    """A citação em prosa nunca abre um bloco — é isso que a âncora em `^` explora.

    Na CF/88 há 513 ocorrências de "Art. N" para 250 artigos: o resto é remissão.
    """
    assert classificar(texto) is None


# --- a fixture: as três redações do art. 6º -------------------------------------------


@pytest.fixture
def art6(fixture_cf88_art6):
    return PARSER.parse(doc_html(URN_CF, fixture_cf88_art6))


def test_redacoes_superadas_viram_dispositivos_versionados(art6):
    """O caso que motiva o esquema `@N`: o mesmo artigo com redações sucessivas."""
    versoes = [d for d in art6.dispositivos if d.tipo is T.ARTIGO]
    # Ordem de documento: da mais antiga para a vigente, que fica sem sufixo.
    assert [d.caminho for d in versoes] == ["art6@1", "art6@2", "art6"]
    assert len({d.id for d in versoes}) == len(versoes), "IDs colidiram entre redações"


def test_apenas_a_redacao_de_2015_esta_vigente(art6):
    """O teste central da fase: parser que não trata `<strike>` indexa 3 versões vivas."""
    vivos = [d for d in art6.dispositivos if d.tipo is T.ARTIGO and d.revogado_em is None]
    assert len(vivos) == 1
    assert "transporte" in vivos[0].texto
    assert vivos[0].vigencia_inicio == date(2015, 1, 1)
    assert "@" not in vivos[0].caminho, "a redação vigente fica com o ID sem sufixo"


def test_redacoes_antigas_nao_mencionam_transporte(art6):
    """É o par vig-01/vig-02 do golden: mesma pergunta, respostas opostas por data."""
    antigas = [d for d in art6.dispositivos if d.tipo is T.ARTIGO and d.revogado_em]
    assert antigas, "nenhuma redação superada capturada"
    assert all("transporte" not in d.texto for d in antigas)


def test_janelas_de_vigencia_encadeiam_sem_buraco(art6):
    """A redação superada morre exatamente quando a seguinte nasce."""
    arts = sorted(
        (d for d in art6.dispositivos if d.tipo is T.ARTIGO),
        key=lambda d: d.vigencia_inicio,
    )
    for anterior, seguinte in zip(arts, arts[1:], strict=False):
        assert anterior.revogado_em == seguinte.vigencia_inicio


def test_texto_riscado_nao_vaza_para_a_redacao_vigente(art6):
    """Cada `<p>` é uma redação. O texto de uma não pode entrar na outra."""
    vivo = next(d for d in art6.dispositivos if d.tipo is T.ARTIGO and d.revogado_em is None)
    assert vivo.texto.count("São direitos sociais") == 1


def test_paragrafo_unico_e_numerado(art6):
    """`parunico`, não `par1`: o artigo com parágrafo único não tem § 1º."""
    par = next(d for d in art6.dispositivos if d.tipo is T.PARAGRAFO)
    assert par.caminho.endswith("parunico")
    assert par.rotulo == "Parágrafo único"
    assert "renda básica familiar" in par.texto


def test_nota_de_alteracao_vira_vigencia(art6):
    """"Redação dada pela EC nº 90, de 2015" precisa virar data, não sobrar como texto."""
    par = next(d for d in art6.dispositivos if d.tipo is T.PARAGRAFO)
    assert par.vigencia_inicio == date(2021, 1, 1)
    assert par.nota_alteracao and "114" in par.nota_alteracao
    assert "Incluído pela" not in par.texto, "a nota tem de sair do texto citável"


def test_vide_nao_altera_vigencia(art6):
    """"(Vide Lei nº 14.601, de 2023)" é remissão, não mudança de redação."""
    par = next(d for d in art6.dispositivos if d.tipo is T.PARAGRAFO)
    assert par.vigencia_inicio == date(2021, 1, 1)
    assert par.revogado_em is None


# --- hierarquia -----------------------------------------------------------------------


def test_hierarquia_titulo_capitulo_artigo():
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "TÍTULO II Dos Direitos e Garantias Fundamentais",
        "CAPÍTULO I Dos Direitos Individuais",
        "Art. 5º Todos são iguais perante a lei.",
    ])))
    art = por_caminho(r, "tit2/cap1/art5")
    assert art is not None and art.tipo is T.ARTIGO
    assert por_caminho(r, "tit2/cap1").id == art.parent_id


def test_inciso_vira_filho_do_artigo():
    """Hierarquia correta é o que permite o parent-document retrieval funcionar."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "Art. 6º São direitos básicos do consumidor:",
        "VIII - a facilitação da defesa de seus direitos;",
    ])))
    inc = por_caminho(r, "art6/inc8")
    assert inc is not None and inc.tipo is T.INCISO
    assert inc.parent_id == por_caminho(r, "art6").id


def test_inciso_depois_de_paragrafo_pendura_no_paragrafo():
    """Nível, não posição: é a hierarquia real do texto legal."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "Art. 7º São direitos dos trabalhadores:",
        "I - relação de emprego protegida;",
        "§ 1º O disposto neste artigo aplica-se:",
        "II - aos servidores ocupantes de cargo público;",
    ])))
    assert por_caminho(r, "art7/inc1").parent_id == por_caminho(r, "art7").id
    assert por_caminho(r, "art7/par1/inc2").parent_id == por_caminho(r, "art7/par1").id


def test_id_do_artigo_ignora_ancestral_estrutural():
    """Regra 1 do `urn`: o capítulo fica no `caminho`, não no ID."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "TÍTULO II Dos Direitos",
        "CAPÍTULO I Dos Direitos Individuais",
        "Art. 5º Todos são iguais.",
    ])))
    assert por_caminho(r, "tit2/cap1/art5").id.endswith("!art5")


def test_adct_nao_colide_com_o_corpo_permanente():
    """134 números de artigo existem nos dois espaços da CF/88."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "Art. 5º Todos são iguais perante a lei.",
        "ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS",
        "Art. 5º Os mandatos do Presidente da República.",
    ])))
    ids = [d.id for d in r.dispositivos if d.tipo is T.ARTIGO]
    assert len(ids) == len(set(ids)) == 2
    assert por_caminho(r, "adct/art5") is not None


def test_adct_nao_dispara_no_link_de_navegacao():
    """O link do topo da página jogava a Constituição inteira para dentro do ADCT."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "Ato das Disposições Constitucionais Transitórias",  # link de navegação
        "Art. 1º A República Federativa do Brasil.",
    ])))
    assert por_caminho(r, "art1") is not None
    assert por_caminho(r, "adct/art1") is None


def test_cabecalho_repetido_nao_vira_redacao_superada():
    """O sumário no fim do Código Civil reapresenta a árvore inteira.

    Versionar um cabeçalho propagava `@1` para os 1847 artigos abaixo dele.
    """
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "TÍTULO I Dos Princípios",
        "Art. 1º A República Federativa do Brasil.",
        "TÍTULO I Dos Princípios",  # entrada de sumário
    ])))
    assert por_caminho(r, "tit1/art1") is not None
    assert not any("@" in d.caminho for d in r.dispositivos)


def test_agrupamento_sem_artigo_e_descartado():
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "TÍTULO I Dos Princípios",
        "Art. 1º A República Federativa do Brasil.",
        "TÍTULO IX Só existe no sumário",
    ])))
    assert por_caminho(r, "tit9") is None
    assert por_caminho(r, "tit1") is not None


def test_arvore_nunca_sai_com_pai_orfao():
    """`dispositivo.parent_id` é FK: um órfão derruba a ingestão inteira."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "TÍTULO I Dos Princípios",
        "CAPÍTULO I Disposições",
        "Art. 1º A República.",
        "I - a soberania;",
        "TÍTULO IX Só no sumário",
        "CAPÍTULO I Também só no sumário",
    ])))
    ids = {d.id for d in r.dispositivos}
    assert {d.parent_id for d in r.dispositivos if d.parent_id} <= ids


def test_bloco_continuacao_anexa_ao_dispositivo_aberto():
    r = PARSER.parse(doc_html(URN_CF, html_planalto([
        "Art. 1º A República Federativa do Brasil,",
        "formada pela união indissolúvel dos Estados e Municípios.",
    ])))
    assert "união indissolúvel" in por_caminho(r, "art1").texto


# --- riscado --------------------------------------------------------------------------


def test_riscado_parcial_e_removido_do_texto_vigente():
    """Expressão substituída dentro de dispositivo que continua valendo."""
    bs = blocos_de("<p>Art. 1º O prazo é de <strike>cinco</strike> sete dias.</p>")
    assert len(bs) == 1
    assert bs[0].superado is False
    assert "cinco" not in bs[0].texto and "sete dias" in bs[0].texto


def test_riscado_integral_marca_o_bloco_sem_descartar():
    """A redação superada é o texto que responde "o que valia em 2010?"."""
    bs = blocos_de("<p><strike>Art. 1º Redação antiga.</strike></p>")
    assert len(bs) == 1 and bs[0].superado is True
    assert "Redação antiga" in bs[0].texto


def test_riscado_sem_data_nao_fica_vigente():
    """Texto morto sem nota: `revogado_em = None` o indexaria como direito vigente."""
    r = PARSER.parse(doc_html(URN_CF, html_planalto(
        ["Art. 1º Texto vivo."], riscados=["Art. 2º Texto morto sem nota."]
    )))
    morto = por_caminho(r, "art2")
    assert morto is not None and morto.revogado_em is not None


# --- remissões ------------------------------------------------------------------------


def test_extrair_remissoes():
    r = PARSER.parse(doc_html(URN_CF, html_planalto(
        ["Art. 100. Aplica-se o disposto no art. 37, § 6º, e no art. 5º, XXXV."]
    )))
    destinos = {x.destino_urn.split("!")[1] for x in r.remissoes}
    assert {"art37_par6", "art5_inc35"} <= destinos


def test_remissao_nao_aponta_para_a_propria_origem():
    disp = PARSER.parse(doc_html(URN_CF, html_planalto(["Art. 5º Nos termos do art. 5º."])))
    art = por_caminho(disp, "art5")
    assert all(x.destino_urn != art.id for x in extrair_remissoes(art.texto, art))
