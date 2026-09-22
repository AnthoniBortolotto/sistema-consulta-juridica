"""Eval ponta a ponta: acurácia de citação, abstenção e o custo da rodada.

Nenhum teste chama o Claude. O serviço é o `Servico` de verdade — `montar`, `responder`,
resolução de citação — com recuperação e geração dublês. O que precisa ser protegido é o
MÉTODO: o que conta como citação correta, quem entra em cada média, e que uma resposta
lida do cache não seja cobrada duas vezes.
"""

from __future__ import annotations

from datetime import date

import pytest

from consulta_juridica.eval.golden import ItemGolden, Mecanismo
from consulta_juridica.eval.metrics import acuracia_citacao, cita_dentro, taxa_abstencao
from consulta_juridica.eval.run import (
    avaliar_ponta_a_ponta,
    custo_de,
    estimar_ponta_a_ponta,
)
from consulta_juridica.generation import prompt as pr
from consulta_juridica.generation.backend import CitacaoBruta, LLMBackend, ResultadoGeracao
from consulta_juridica.generation.cache import BackendComCache
from consulta_juridica.generation.citacoes import faixas
from consulta_juridica.models import Citacao, MotivoAbstencao, Resposta, Uso
from consulta_juridica.retrieval.expansao import Nivel, expandir
from consulta_juridica.service import Servico

from .conftest import URN_CDC, candidatos_de, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
INC2 = id_cdc("tit1", "cap3", "art6", "inc2")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")
REF = date(2026, 1, 1)


def citacao(dispositivo_id: str) -> Citacao:
    return Citacao(
        dispositivo_id=dispositivo_id, rotulo_completo="r", texto_citado="t", fonte_url="u"
    )


# --- o que é citação correta ----------------------------------------------------------


def test_citar_dentro_do_esperado_e_correto():
    """O golden anota "art. 49" e a citação cai no parágrafo único dele: é texto do
    dispositivo esperado, citado com mais precisão do que se anotou."""
    assert cita_dentro(f"{URN_CDC}!art49_parunico", f"{URN_CDC}!art49")
    assert cita_dentro(INC8, ART6)


def test_citar_a_moldura_nao_e_correto():
    """Esperar o inciso VIII e receber o caput do art. 6º é citar a moldura, não o que
    sustenta a afirmação."""
    assert not cita_dentro(ART6, INC8)


def test_art1_nao_e_pai_de_art10():
    """O separador `_` é o que impede prefixo numérico de virar parentesco."""
    assert not cita_dentro(f"{URN_CDC}!art10", f"{URN_CDC}!art1")
    assert not cita_dentro(f"{URN_CDC}!art10_inc1", f"{URN_CDC}!art1")


def test_redacao_superada_conta_como_o_dispositivo():
    """Em `vig-02`, a resposta certa para 2010 cita `art6@3` — e o golden anota `art6`."""
    cf = "urn:lex:br:federal:constituicao:1988-10-05;1988"
    assert cita_dentro(f"{cf}!art6@3", f"{cf}!art6")


def test_acuracia_e_a_fracao_das_citacoes():
    assert acuracia_citacao([citacao(INC8), citacao(INC2)], [INC8]) == 0.5


def test_responder_sem_citar_vale_zero():
    """Uma afirmação jurídica sem citação é exatamente o que este sistema existe para não
    produzir — não pode sair como "não se aplica"."""
    assert acuracia_citacao([], [INC8]) == 0.0


def test_taxa_de_abstencao():
    def resposta(abstencao):
        return Resposta(
            texto="x", data_referencia=REF, modelo="m", versao_prompt="v1", abstencao=abstencao
        )

    respostas = [resposta(None), resposta(MotivoAbstencao.CONTEXTO_INSUFICIENTE)]
    assert taxa_abstencao(respostas) == 0.5
    assert taxa_abstencao([]) == 0.0


# --- custo ----------------------------------------------------------------------------


def test_custo_usa_a_tabela_de_precos():
    uso = Uso(tokens_entrada=1_000_000, tokens_saida=1_000_000)
    assert custo_de(uso, "claude-opus-5") == pytest.approx(30.0)
    assert custo_de(uso, "claude-haiku-4-5") == pytest.approx(6.0)


def test_leitura_de_cache_custa_um_decimo():
    assert custo_de(Uso(tokens_cache_leitura=1_000_000), "claude-opus-5") == pytest.approx(0.5)


def test_modelo_fora_da_tabela_nao_tem_custo_chutado():
    """`None`, não zero: zero diria que foi de graça."""
    assert custo_de(Uso(tokens_entrada=10), "modelo-desconhecido") is None


# --- a rodada -------------------------------------------------------------------------


@pytest.fixture
def trecho(corpus_cdc, arvore_cdc, norma_cdc):
    (t,) = expandir(
        corpus_cdc,
        candidatos_de(arvore_cdc, norma_cdc, [INC8]),
        nivel=Nivel.ARTIGO,
        data_referencia=REF,
    )
    return t


class RecuperadorFixo:
    """Devolve o mesmo trecho para toda pergunta, menos as listadas em `vazias`."""

    def __init__(self, trecho, conn, vazias=()):
        self.trecho, self.conn, self.vazias = trecho, conn, set(vazias)

    def recuperar(self, consulta, criterios, **kw):
        return [] if consulta in self.vazias else [self.trecho]


class GeradorRoteirizado(LLMBackend):
    """Responde por pergunta, como o modelo responderia. Conta as chamadas."""

    nome = "roteiro"
    suporta_citacoes = True

    def __init__(self, trecho, roteiro: dict[str, str]):
        self.trecho, self.roteiro, self.chamadas = trecho, roteiro, 0

    def gerar(self, pedido) -> ResultadoGeracao:
        self.chamadas += 1
        acao = self.roteiro[pedido.pergunta]
        if acao == "abster":
            return ResultadoGeracao(f"{pr.MARCA_ABSTENCAO}\nfaltou.", [], "claude-opus-5", Uso())
        if acao == "sem_citar":
            return ResultadoGeracao("Sim.", [], "claude-opus-5", Uso())
        ini, fim, _ = next(f for f in faixas(self.trecho) if f[2] == acao)
        bruta = CitacaoBruta("D1", self.trecho.texto[ini:fim], ini, fim)
        uso = Uso(tokens_entrada=10_000, tokens_saida=1_000)
        return ResultadoGeracao("Sim.", [bruta], "claude-opus-5", uso)


def item(id, pergunta, esperados, *, deve_abster=False, mecanismo="lexical") -> ItemGolden:
    return ItemGolden(
        id=id,
        mecanismo=Mecanismo(mecanismo),
        pergunta=pergunta,
        data_referencia=REF,
        dispositivos_esperados=esperados,
        rotulos_esperados=esperados,
        deve_abster=deve_abster,
    )


def servico(trecho, conn, roteiro, *, tmp_path=None, vazias=()) -> Servico:
    backend: LLMBackend = GeradorRoteirizado(trecho, roteiro)
    if tmp_path is not None:
        backend = BackendComCache(backend, tmp_path)
    return Servico(
        recuperador=RecuperadorFixo(trecho, conn, vazias), backend=backend, modelo="claude-opus-5"
    )


def test_backend_sem_citations_nativas_e_recusado(trecho, corpus_cdc):
    """Um número inválido no README é pior que nenhum."""
    svc = servico(trecho, corpus_cdc, {})
    svc.backend.suporta_citacoes = False
    with pytest.raises(ValueError, match="citations nativas"):
        avaliar_ponta_a_ponta(svc, [])
    with pytest.raises(ValueError, match="citations nativas"):
        estimar_ponta_a_ponta(svc, [])


def test_acuracia_so_conta_quem_respondeu_e_devia(trecho, corpus_cdc):
    """A abstenção indevida já é falha, medida na taxa própria; somá-la como zero na
    acurácia contaria a mesma falha duas vezes."""
    svc = servico(trecho, corpus_cdc, {"certa": INC8, "absteve": "abster"})
    rel = avaliar_ponta_a_ponta(
        svc, [item("t-1", "certa", [INC8]), item("t-2", "absteve", [INC8])]
    )
    assert rel.acuracia_citacao == 1.0
    assert rel.abstencao_indevida == 0.5
    assert [r.acuracia for r in rel.itens] == [1.0, None]


def test_citacao_errada_derruba_a_acuracia(trecho, corpus_cdc):
    svc = servico(trecho, corpus_cdc, {"p": INC2})
    rel = avaliar_ponta_a_ponta(svc, [item("t-1", "p", [INC8])])
    assert rel.acuracia_citacao == 0.0
    assert rel.itens[0].corretas == 0


def test_responder_sem_citar_aparece_no_relatorio(trecho, corpus_cdc):
    svc = servico(trecho, corpus_cdc, {"p": "sem_citar"})
    rel = avaliar_ponta_a_ponta(svc, [item("t-1", "p", [INC8])])
    assert rel.sem_citacao == 1
    assert rel.acuracia_citacao == 0.0


def test_abstencao_correta_e_indevida_se_medem_juntas(trecho, corpus_cdc):
    """Cada uma sozinha se ganha trapaceando: um sistema que sempre se abstém tem
    `abstencao_correta` 1.0. É o par que diz se ele está calibrado."""
    svc = servico(
        trecho, corpus_cdc, {"a": "abster", "b": "abster", "c": INC8}
    )
    rel = avaliar_ponta_a_ponta(
        svc,
        [
            item("abs-1", "a", [], deve_abster=True, mecanismo="abstencao"),
            item("lex-1", "b", [INC8]),
            item("lex-2", "c", [INC8]),
        ],
    )
    assert rel.abstencao_correta == 1.0
    assert rel.abstencao_indevida == 0.5
    assert rel.taxa_abstencao == pytest.approx(2 / 3)


def test_responder_quando_devia_se_abster_e_falha(trecho, corpus_cdc):
    svc = servico(trecho, corpus_cdc, {"a": INC8})
    rel = avaliar_ponta_a_ponta(
        svc, [item("abs-1", "a", [], deve_abster=True, mecanismo="abstencao")]
    )
    assert rel.abstencao_correta == 0.0
    assert rel.itens[0].acuracia is None, "não há esperado contra o qual medir"


def test_sem_candidatos_nao_chama_nem_cobra(trecho, corpus_cdc):
    svc = servico(trecho, corpus_cdc, {}, vazias={"vazia"})
    rel = avaliar_ponta_a_ponta(
        svc, [item("abs-1", "vazia", [], deve_abster=True, mecanismo="abstencao")]
    )
    assert svc.backend.chamadas == 0
    assert rel.chamadas_pagas == 0
    assert rel.itens[0].abstencao == "sem_candidatos"


def test_o_cache_nao_e_cobrado_duas_vezes(trecho, corpus_cdc, tmp_path):
    """O `uso` de uma resposta lida do cache é o da chamada original, feita dias antes.
    Somá-lo ao custo desta rodada contaria duas vezes o mesmo gasto."""
    itens = [item("t-1", "p", [INC8])]
    primeira = avaliar_ponta_a_ponta(
        servico(trecho, corpus_cdc, {"p": INC8}, tmp_path=tmp_path), itens
    )
    segunda = avaliar_ponta_a_ponta(
        servico(trecho, corpus_cdc, {"p": INC8}, tmp_path=tmp_path), itens
    )
    assert (primeira.chamadas_pagas, segunda.chamadas_pagas) == (1, 0)
    assert primeira.custo_estimado_usd == pytest.approx(0.075)
    assert segunda.custo_estimado_usd == 0.0
    assert segunda.acuracia_citacao == primeira.acuracia_citacao


def test_o_relatorio_diz_quem_respondeu_de_fato(trecho, corpus_cdc):
    """O modelo da resposta, não o configurado: numa rodada com cache de uma configuração
    anterior, os dois aparecem."""
    rel = avaliar_ponta_a_ponta(
        servico(trecho, corpus_cdc, {"p": INC8}), [item("t-1", "p", [INC8])]
    )
    assert rel.modelo == "claude-opus-5"
    assert rel.versao_prompt == pr.VERSAO_PROMPT


# --- estimativa -----------------------------------------------------------------------


def test_estimativa_nao_chama_o_modelo(trecho, corpus_cdc, tmp_path):
    svc = servico(trecho, corpus_cdc, {"p": INC8}, tmp_path=tmp_path)
    est = estimar_ponta_a_ponta(svc, [item("t-1", "p", [INC8])])
    assert svc.backend.interno.chamadas == 0
    assert est.a_pagar == 1
    assert est.tokens_entrada > 0
    assert 0 < est.custo_min_usd < est.custo_max_usd


def test_estimativa_sabe_o_que_ja_esta_no_cache(trecho, corpus_cdc, tmp_path):
    """Reexecutar não re-cobra o que não mudou — e a estimativa tem de saber disso, senão
    ela assusta à toa na segunda rodada."""
    itens = [item("t-1", "p", [INC8]), item("t-2", "q", [INC8])]
    roteiro = {"p": INC8, "q": INC8}
    avaliar_ponta_a_ponta(servico(trecho, corpus_cdc, roteiro, tmp_path=tmp_path), itens[:1])
    est = estimar_ponta_a_ponta(
        servico(trecho, corpus_cdc, roteiro, tmp_path=tmp_path), itens
    )
    assert (est.do_cache, est.a_pagar) == (1, 1)


def test_estimativa_conta_o_que_sai_de_graca(trecho, corpus_cdc):
    svc = servico(trecho, corpus_cdc, {}, vazias={"vazia"})
    est = estimar_ponta_a_ponta(svc, [item("t-1", "vazia", [INC8])])
    assert (est.sem_candidatos, est.a_pagar) == (1, 0)
    assert est.custo_max_usd == 0.0
