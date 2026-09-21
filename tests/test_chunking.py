"""Estratégias de chunking.

A unidade é estrutural, nunca uma janela de tokens. O que estes testes protegem é a
diferença entre o texto INDEXADO (com contexto, para o embedding achar) e o texto
CITÁVEL (cru, para a citação bater com a fonte) — confundi-los quebra a promessa central
do sistema em direções opostas: contexto no citável inventa texto que a lei não tem;
citável no indexado torna metade do corpus irrecuperável.
"""

from __future__ import annotations

from datetime import date

import pytest

from consulta_juridica.ingest.chunking import (
    Arvore,
    ChunkPorArtigo,
    ChunkPorDispositivo,
    obter_estrategia,
    texto_contextualizado,
)
from consulta_juridica.models import TipoDispositivo as T
from consulta_juridica.urn import montar_urn_dispositivo
from consulta_juridica.vectorstore import SENTINELA_VIGENTE, id_ponto

from .conftest import URN_CDC, disp

ART6 = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6")
INC8 = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6/inc8")
INC4 = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6/inc4")


def por_id(chunks):
    return {c.payload.dispositivo_id: c for c in chunks}


@pytest.fixture
def chunks_dispositivo(arvore_cdc, norma_cdc):
    return por_id(ChunkPorDispositivo().chunks(arvore_cdc, norma_cdc))


@pytest.fixture
def chunks_artigo(arvore_cdc, norma_cdc):
    return por_id(ChunkPorArtigo().chunks(arvore_cdc, norma_cdc))


# --- granularidade --------------------------------------------------------------------


def test_nenhum_chunk_parte_dispositivo(chunks_dispositivo, arvore_cdc):
    """A unidade é estrutural: todo texto citável é o texto íntegro de um dispositivo."""
    textos = {d.texto for d in arvore_cdc}
    assert all(c.texto in textos for c in chunks_dispositivo.values())


def test_artigo_com_filhos_tambem_vira_chunk(chunks_dispositivo):
    """Folha estrita deixaria 199 KB de caput fora do índice — e o art. 927 do CC,
    que o golden `lex-02` espera, não viraria chunk nenhum."""
    assert ART6 in chunks_dispositivo
    assert chunks_dispositivo[ART6].texto == "São direitos básicos do consumidor:"


def test_agrupamento_nao_vira_chunk(chunks_dispositivo):
    """Título e capítulo organizam o texto; a epígrafe deles entra como contexto, não
    como chunk próprio."""
    tit = montar_urn_dispositivo(URN_CDC, "tit1")
    assert tit not in chunks_dispositivo


def test_dispositivo_sem_texto_nao_vira_chunk(arvore_cdc, norma_cdc):
    vazio = disp(URN_CDC, ["tit1", "cap3", "art9"], T.ARTIGO, "Art. 9º", "   ", ordem=9)
    chunks = por_id(ChunkPorDispositivo().chunks([*arvore_cdc, vazio], norma_cdc))
    assert vazio.id not in chunks


def test_texto_revogado_nao_vira_chunk(arvore_cdc, norma_cdc):
    """São 29 no corpus. Indexar um chunk cujo conteúdo é a palavra "revogado" é ruído."""
    marca = disp(URN_CDC, ["tit1", "cap3", "art9"], T.ARTIGO, "Art. 9º", "(revogado).", ordem=9)
    chunks = por_id(ChunkPorDispositivo().chunks([*arvore_cdc, marca], norma_cdc))
    assert marca.id not in chunks


# --- texto indexado x texto citável ---------------------------------------------------


def test_texto_indexado_carrega_hierarquia(chunks_dispositivo):
    """`texto_indexado` precisa do caminho; `texto` fica cru, é ele que será citado."""
    c = chunks_dispositivo[INC8]
    assert "CDC" in c.texto_indexado
    assert "Dos Direitos Básicos" in c.texto_indexado
    assert c.texto_indexado.startswith("CDC > ")


def test_texto_indexado_carrega_o_caput_do_artigo(chunks_dispositivo):
    """15% das folhas do corpus têm menos de 60 chars. Sem o caput, o vetor é vazio de
    informação: "VI - defesa da paz;" não recupera nada."""
    c = chunks_dispositivo[INC8]
    assert "São direitos básicos do consumidor:" in c.texto_indexado
    assert "São direitos básicos" not in c.texto, "o caput não pode entrar no texto citável"


def test_texto_citavel_e_identico_a_fonte(chunks_dispositivo, arvore_cdc):
    """Citação tem de bater com a fonte caractere a caractere, senão não é verificável."""
    original = next(d for d in arvore_cdc if d.id == INC8)
    assert chunks_dispositivo[INC8].texto == original.texto


def test_inciso_leva_travessao_no_indexado(chunks_dispositivo):
    """A lei escreve "VIII - a facilitação", não "VIII a facilitação"."""
    assert "VIII - a facilitação" in chunks_dispositivo[INC8].texto_indexado


def test_texto_contextualizado_sem_ancestral(norma_cdc, arvore_cdc):
    d = next(x for x in arvore_cdc if x.id == ART6)
    esperado = "CDC\nArt. 6º São direitos básicos do consumidor:"
    assert texto_contextualizado(d, [], norma_cdc) == esperado


# --- payload --------------------------------------------------------------------------


def test_payload_tem_rotulo_completo(chunks_dispositivo):
    assert chunks_dispositivo[INC8].payload.rotulo_completo == "Lei 8.078/1990, Art. 6º, VIII"


def test_payload_usa_sentinela_para_nao_revogado(chunks_dispositivo):
    """`range(gt=ref)` no Qdrant exclui ponto sem o campo: sem sentinela o vigente sumia."""
    assert chunks_dispositivo[INC8].payload.revogado_em_dia == SENTINELA_VIGENTE
    assert chunks_dispositivo[INC4].payload.revogado_em_dia == date(2020, 1, 1).toordinal()


def test_payload_nunca_fica_sem_vigencia_inicio(chunks_dispositivo, norma_cdc):
    """Campo ausente sumiria com o chunk no filtro, em vez de dar erro."""
    esperado = norma_cdc.data_publicacao.toordinal()
    assert chunks_dispositivo[INC8].payload.vigencia_inicio_dia == esperado


def test_id_do_chunk_e_deterministico(chunks_dispositivo):
    """Sem uuid5 do dispositivo_id, cada reindexação duplicaria o corpus."""
    assert chunks_dispositivo[INC8].id == id_ponto(INC8)


def test_ids_nao_colidem(chunks_dispositivo):
    ids = [c.id for c in chunks_dispositivo.values()]
    assert len(ids) == len(set(ids))


# --- estratégia por artigo ------------------------------------------------------------


def test_chunk_por_artigo_inclui_filhos(chunks_artigo):
    c = chunks_artigo[ART6]
    assert "São direitos básicos do consumidor:" in c.texto
    assert "VIII - a facilitação" in c.texto
    assert "II - a educação" in c.texto


def test_chunk_por_artigo_monta_em_ordem_de_documento(chunks_artigo):
    """Ordenar por caminho colocaria o inciso X antes do II."""
    texto = chunks_artigo[ART6].texto
    assert texto.index("II - a educação") < texto.index("X - a adequada")


def test_chunk_por_artigo_exclui_filho_revogado_antes_do_artigo(chunks_artigo):
    """Um chunk tem uma janela de vigência só; texto revogado dentro dele seria
    apresentado como direito vigente."""
    assert "inciso hipotético revogado" not in chunks_artigo[ART6].texto


def test_chunk_por_artigo_nao_emite_agrupamento(chunks_artigo):
    assert montar_urn_dispositivo(URN_CDC, "tit1") not in chunks_artigo
    assert set(chunks_artigo) == {ART6}


def test_as_duas_estrategias_usam_o_mesmo_id_para_o_artigo(chunks_dispositivo, chunks_artigo):
    """Trocar de estratégia não pode mudar a identidade do ponto no Qdrant."""
    assert chunks_dispositivo[ART6].id == chunks_artigo[ART6].id


# --- árvore e registro ----------------------------------------------------------------


def test_arvore_ancestrais(arvore_cdc):
    arv = Arvore(arvore_cdc)
    inc = next(d for d in arvore_cdc if d.id == INC8)
    assert [a.caminho for a in arv.ancestrais(inc)] == ["tit1", "tit1/cap3", "tit1/cap3/art6"]


def test_arvore_descendentes_em_ordem_de_documento(arvore_cdc):
    arv = Arvore(arvore_cdc)
    art = next(d for d in arvore_cdc if d.id == ART6)
    assert [d.rotulo for d in arv.descendentes(art)] == ["II", "IV", "VIII", "X", "XI"]


@pytest.mark.parametrize("nome", ["dispositivo", "artigo"])
def test_obter_estrategia(nome):
    assert obter_estrategia(nome).nome == nome


def test_obter_estrategia_desconhecida_levanta():
    with pytest.raises(ValueError, match="desconhecida"):
        obter_estrategia("janela-de-512-tokens")
