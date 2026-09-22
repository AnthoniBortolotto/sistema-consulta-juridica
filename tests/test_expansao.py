"""Expansão inciso -> artigo.

O teste que importa é o do vazamento de vigência: o filtro do Qdrant exclui o inciso
revogado, mas a expansão lê o artigo inteiro do SQLite. Sem reaplicar a data aqui, os
irmãos revogados voltam pela porta dos fundos e chegam ao modelo como direito vigente.

Tudo aqui roda contra SQLite em memória: a expansão não fala com o Qdrant, e é essa
separação que permite testá-la sem servidor nenhum.
"""

from __future__ import annotations

from datetime import date

import pytest

from consulta_juridica.retrieval.expansao import MARCA_OMISSAO, Nivel, expandir

from .conftest import URN_CDC, candidatos_de, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
CAP3 = id_cdc("tit1", "cap3")
INC2 = id_cdc("tit1", "cap3", "art6", "inc2")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")
INC10 = id_cdc("tit1", "cap3", "art6", "inc10")

TEXTO_REVOGADO = "inciso hipotético revogado"
TEXTO_VACATIO = "só entra em vigor em 2030"


@pytest.fixture
def cands(arvore_cdc, norma_cdc):
    """Fábrica de candidatos sobre a árvore do CDC."""

    def fabricar(*ids: str, scores=()):
        return candidatos_de(arvore_cdc, norma_cdc, ids, scores)

    return fabricar


# --- subir na hierarquia --------------------------------------------------------------


def test_expansao_sobe_do_inciso_ao_artigo(corpus_cdc, cands, data_ref):
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert trecho.dispositivo_id == ART6
    assert trecho.texto.startswith("Art. 6º São direitos básicos do consumidor:")
    assert "VIII - a facilitação da defesa" in trecho.texto


def test_expansao_respeita_a_ordem_de_documento(corpus_cdc, cands, data_ref):
    """"inc10" vem antes de "inc2" lexicograficamente. O artigo chegaria ao modelo com os
    incisos embaralhados se a ordem viesse do `caminho`."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    posicoes = [trecho.texto.index(r) for r in ("II - ", "VIII - ", "X - ")]
    assert posicoes == sorted(posicoes)


def test_nivel_nenhum_devolve_o_proprio_dispositivo(corpus_cdc, cands, data_ref):
    """Existe para o eval medir a recuperação sem parent-document retrieval."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.NENHUM, data_referencia=data_ref
    )
    assert trecho.dispositivo_id == INC8
    assert trecho.texto.startswith("VIII - a facilitação")
    assert "São direitos básicos" not in trecho.texto


def test_nivel_secao_cai_para_artigo_quando_a_norma_nao_usa_secoes(corpus_cdc, cands, data_ref):
    """Degradar é melhor que devolver nada: o candidato foi recuperado, há o que mostrar."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.SECAO, data_referencia=data_ref
    )
    assert trecho.dispositivo_id == ART6


def test_dispositivo_estrutural_expande_para_si_mesmo(corpus_cdc, arvore_cdc, norma_cdc, data_ref):
    """Um capítulo não está dentro de artigo nenhum — `artigo_ancestral` devolve None e a
    expansão não pode explodir por causa disso."""
    from consulta_juridica.retrieval.busca import Candidato

    chunk = next(c for c in candidatos_de(arvore_cdc, norma_cdc, [ART6]))
    estrutural = Candidato(
        chunk_id=chunk.chunk_id,
        dispositivo_id=CAP3,
        texto="Dos Direitos Básicos",
        payload=chunk.payload,
        score=1.0,
    )
    (trecho,) = expandir(
        corpus_cdc, [estrutural], nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert trecho.dispositivo_id == CAP3


def test_candidato_sem_dispositivo_no_sqlite_e_descartado(corpus_cdc, cands, data_ref):
    """Índice mais velho que o corpus. Trecho sem texto chegaria ao modelo como citação
    vazia; o conserto é reindexar, e o índice é derivado e reconstruível."""
    fantasma = cands(INC8)
    fantasma[0].dispositivo_id = f"{URN_CDC}!art999"
    assert expandir(corpus_cdc, fantasma, nivel=Nivel.ARTIGO, data_referencia=data_ref) == []


# --- vigência -------------------------------------------------------------------------


def test_expansao_omite_irmaos_revogados(corpus_cdc, cands, data_ref):
    """O caso que motiva `data_referencia` ser obrigatório em `expandir`."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert TEXTO_REVOGADO not in trecho.texto
    assert TEXTO_VACATIO not in trecho.texto


def test_expansao_traz_o_irmao_que_ainda_vigorava_na_data(corpus_cdc, cands):
    """A mesma pergunta em 2019 tem outra resposta, e é para isso que o sistema existe."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=date(2019, 1, 1)
    )
    assert TEXTO_REVOGADO in trecho.texto


def test_o_proprio_alvo_entra_mesmo_revogado(corpus_cdc, arvore_cdc, norma_cdc, data_ref):
    """Numa consulta com `incluir_revogados`, o dispositivo revogado é exatamente o que se
    quer ler. Quem passa pelo filtro de vigência são os descendentes, não o alvo."""
    inc4 = id_cdc("tit1", "cap3", "art6", "inc4")
    (trecho,) = expandir(
        corpus_cdc,
        candidatos_de(arvore_cdc, norma_cdc, [inc4]),
        nivel=Nivel.NENHUM,
        data_referencia=data_ref,
    )
    assert TEXTO_REVOGADO in trecho.texto


# --- deduplicação ---------------------------------------------------------------------


def test_incisos_do_mesmo_artigo_viram_um_trecho_so(corpus_cdc, cands, data_ref):
    """A fusão acontece na expansão, não depois: os dois incisos precisam estar no MESMO
    texto, e fundir dois textos já montados obrigaria a jogar um fora."""
    trechos = expandir(
        corpus_cdc, cands(INC8, INC2, INC10), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert [t.dispositivo_id for t in trechos] == [ART6]
    assert {INC2, INC8, INC10} <= set(trechos[0].dispositivos)


def test_o_trecho_fundido_fica_com_o_melhor_score(corpus_cdc, cands, data_ref):
    (trecho,) = expandir(
        corpus_cdc,
        cands(INC8, INC2, scores=[0.3, 0.9]),
        nivel=Nivel.ARTIGO,
        data_referencia=data_ref,
    )
    assert trecho.score_fusao == 0.9


def test_a_ordem_dos_trechos_e_a_do_ranking(corpus_cdc, cands, data_ref):
    """Reordenar aqui desfaria o trabalho do reranker."""
    trechos = expandir(
        corpus_cdc, cands(INC8, INC2), nivel=Nivel.NENHUM, data_referencia=data_ref
    )
    assert [t.dispositivo_id for t in trechos] == [INC8, INC2]


# --- o que vai para o modelo ----------------------------------------------------------


def test_trecho_carrega_rotulo_e_url_para_o_usuario_conferir(corpus_cdc, cands, data_ref):
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert trecho.rotulo_completo == "Lei 8.078/1990, Art. 6º"
    assert trecho.fonte_url.startswith("https://www.planalto.gov.br/")


def test_score_do_trecho_e_o_que_ordenou_de_fato(corpus_cdc, cands, data_ref):
    """Sem rerank o score do trecho é o da fusão; com rerank, o do cross-encoder."""
    (sem_rerank,) = expandir(
        corpus_cdc, cands(INC8, scores=[0.25]), nivel=Nivel.NENHUM, data_referencia=data_ref
    )
    assert (sem_rerank.score, sem_rerank.score_fusao, sem_rerank.score_rerank) == (0.25, 0.25, None)

    com = cands(INC8, scores=[0.25])
    com[0].score_rerank = 7.5
    (trecho,) = expandir(corpus_cdc, com, nivel=Nivel.NENHUM, data_referencia=data_ref)
    assert (trecho.score, trecho.score_fusao, trecho.score_rerank) == (7.5, 0.25, 7.5)


def test_corte_por_max_chars_respeita_a_fronteira_do_dispositivo(corpus_cdc, cands, data_ref):
    """Artigo cortado no meio de uma frase chega ao modelo como texto legal mutilado, e a
    citação que sair dali não bate com a fonte."""
    (inteiro,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    (cortado,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref, max_chars=120
    )
    assert len(cortado.texto) < len(inteiro.texto)
    linhas = set(cortado.texto.splitlines()) - {MARCA_OMISSAO}
    assert linhas <= set(inteiro.texto.splitlines()), "nenhuma linha foi cortada ao meio"


def test_o_alvo_vai_inteiro_mesmo_estourando_o_limite(corpus_cdc, cands, data_ref):
    """Nunca se corta o texto que a busca escolheu."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.NENHUM, data_referencia=data_ref, max_chars=10
    )
    assert trecho.texto.endswith("no processo civil;")


def test_nivel_nenhum_nao_pendura_os_filhos_do_artigo(corpus_cdc, cands, data_ref):
    """"Nenhum" tem de ser nenhum. O artigo recuperado pelo caput vindo com os incisos
    juntos é `ChunkPorArtigo` com outro nome, e o eval acharia estar medindo a recuperação
    crua quando estaria medindo a estratégia de chunking."""
    (trecho,) = expandir(
        corpus_cdc, cands(ART6), nivel=Nivel.NENHUM, data_referencia=data_ref
    )
    assert trecho.texto == "Art. 6º São direitos básicos do consumidor:"


def test_trecho_registra_os_dispositivos_que_o_compoem(corpus_cdc, cands, data_ref):
    """É esta lista que responde, no eval, se o esperado chegou ao modelo: com expansão até
    o artigo, o inciso anotado no golden está DENTRO do trecho, não é ele."""
    (trecho,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert trecho.dispositivos[0] == ART6
    assert INC8 in trecho.dispositivos
    assert id_cdc("tit1", "cap3", "art6", "inc4") not in trecho.dispositivos


def test_o_dispositivo_que_a_busca_achou_nunca_e_cortado(corpus_cdc, cands, data_ref):
    """O defeito que o eval da fase 6 pegou: com `max_chars` estourado, o art. 5º da CF era
    cortado antes do inciso LXXVIII, e `lex-01` recuperava o artigo certo com o texto
    errado — recall 0 com o dispositivo em primeiro lugar."""
    (cortado,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref, max_chars=120
    )
    assert INC8 in cortado.dispositivos
    assert "a facilitação da defesa" in cortado.texto
    assert INC10 not in cortado.dispositivos


def test_o_que_foi_cortado_deixa_marca(corpus_cdc, cands, data_ref):
    """Sem a marca, o modelo leria dois incisos distantes como consecutivos."""
    (cortado,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref, max_chars=120
    )
    assert MARCA_OMISSAO in cortado.texto
    assert cortado.texto.startswith("Art. 6º São direitos")


def test_sem_corte_nao_ha_marca(corpus_cdc, cands, data_ref):
    (inteiro,) = expandir(
        corpus_cdc, cands(INC8), nivel=Nivel.ARTIGO, data_referencia=data_ref
    )
    assert MARCA_OMISSAO not in inteiro.texto
