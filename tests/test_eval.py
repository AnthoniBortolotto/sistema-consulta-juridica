"""Golden set, métricas e a execução do eval.

As métricas são funções puras e não precisam de corpus. O que precisa de banco é a
validação do golden — e é ela que impede o erro mais caro do eval: um esperado anotado
errado, que derruba o recall em silêncio e faz parecer defeito da recuperação.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from consulta_juridica.eval.golden import ItemGolden, Mecanismo, carregar, validar
from consulta_juridica.eval.metrics import mrr, recall_em_k
from consulta_juridica.eval.run import avaliar_recuperacao
from consulta_juridica.models import Trecho
from consulta_juridica.retrieval.expansao import Nivel

from .conftest import URN_CDC, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")
ART6_CF = "urn:lex:br:federal:constituicao:1988-10-05;1988!art6"

# --- métricas -------------------------------------------------------------------------


def test_recall_conta_trechos_e_nao_dispositivos():
    """O k é em TRECHOS. Achatar os dispositivos numa lista só faria recall@5 medir os
    cinco primeiros incisos do primeiro artigo."""
    ranking = [["a", "b", "c", "d", "e", "f"], ["alvo"]]
    assert recall_em_k(["alvo"], ranking, k=1) == 0.0
    assert recall_em_k(["alvo"], ranking, k=2) == 1.0


def test_recall_e_fracao_dos_esperados():
    ranking = [["x"], ["y"]]
    assert recall_em_k(["x", "y", "z"], ranking, k=2) == pytest.approx(2 / 3)


def test_recall_ignora_o_sufixo_de_redacao():
    """O golden anota o dispositivo ("CF/88 art. 6º"); qual redação responde é o que a data
    de referência decide. Sem isto, `vig-02` contaria erro por devolver `art6@3` — que é
    exatamente a resposta certa para 2010."""
    assert recall_em_k([ART6_CF], [[f"{ART6_CF}@3"]], k=1) == 1.0


def test_item_sem_esperados_nao_pune_a_media():
    """Os de abstenção. Devolver 0.0 puxaria a média para baixo punindo o sistema por uma
    pergunta que ele nem deveria responder."""
    assert recall_em_k([], [["qualquer"]], k=5) == 1.0
    assert mrr([], []) == 1.0


def test_mrr_e_o_reciproco_da_posicao():
    ranking = [["a"], ["b"], ["alvo"]]
    assert mrr(["alvo"], ranking) == pytest.approx(1 / 3)
    assert mrr(["ausente"], ranking) == 0.0


def test_mrr_usa_o_primeiro_acerto():
    ranking = [["a"], ["x", "y"], ["x"]]
    assert mrr(["x", "y"], ranking) == 0.5


# --- golden ---------------------------------------------------------------------------


def escrever(tmp_path: Path, *linhas: str) -> Path:
    caminho = tmp_path / "g.jsonl"
    caminho.write_text("\n".join(linhas), encoding="utf-8")
    return caminho


def item(**campos) -> str:
    base = {
        "id": "t-01",
        "mecanismo": "lexical",
        "pergunta": "p",
        "data_referencia": "2026-01-01",
        "dispositivos_esperados": ["CDC art. 6º VIII"],
    }
    import json

    return json.dumps({**base, **campos}, ensure_ascii=False)


def test_carregar_resolve_o_rotulo_para_id_canonico(tmp_path):
    """O arquivo continua legível para ser anotado à mão; quem normaliza é o carregamento."""
    (carregado,) = carregar(escrever(tmp_path, item()))
    assert carregado.dispositivos_esperados == [INC8]
    assert carregado.rotulos_esperados == ["CDC art. 6º VIII"]


def test_carregar_recusa_rotulo_que_nao_resolve(tmp_path):
    """Nunca devolve um ID chutado: um esperado errado derruba o recall em silêncio."""
    with pytest.raises(ValueError, match="t-01"):
        carregar(escrever(tmp_path, item(dispositivos_esperados=["Lei das Bruxas art. 1º"])))


def test_carregar_recusa_id_repetido(tmp_path):
    with pytest.raises(ValueError, match="repetido"):
        carregar(escrever(tmp_path, item(), item()))


def test_carregar_aponta_a_linha_do_erro(tmp_path):
    with pytest.raises(ValueError, match="g.jsonl:2"):
        carregar(escrever(tmp_path, item(), item(id="t-02", mecanismo="inexistente")))


def test_validar_aceita_o_golden_intacto(corpus_cdc, tmp_path):
    assert validar(carregar(escrever(tmp_path, item())), corpus_cdc) == []


def test_validar_acusa_esperado_fora_do_corpus(corpus_cdc, tmp_path):
    itens = carregar(escrever(tmp_path, item(dispositivos_esperados=["CDC art. 999"])))
    (problema,) = validar(itens, corpus_cdc)
    assert "não existe no corpus" in problema


def test_validar_acusa_esperado_que_nao_vigorava_na_data(corpus_cdc, tmp_path):
    """O inverso do erro que o sistema evita: anotar como certa a resposta que não vigorava
    faz a recuperação parecer quebrada quando ela acertou."""
    itens = carregar(escrever(tmp_path, item(dispositivos_esperados=["CDC art. 6º IV"])))
    (problema,) = validar(itens, corpus_cdc)
    assert "não vigorava" in problema


def test_validar_aceita_redacao_superada_na_data_certa(corpus_cdc, tmp_path):
    """Com data anterior à revogação, o mesmo item passa."""
    itens = carregar(
        escrever(
            tmp_path,
            item(dispositivos_esperados=["CDC art. 6º IV"], data_referencia="2019-01-01"),
        )
    )
    assert validar(itens, corpus_cdc) == []


def test_validar_acusa_contradicao_com_deve_abster(corpus_cdc, tmp_path):
    itens = carregar(escrever(tmp_path, item(deve_abster=True)))
    assert any("deve_abster" in p for p in validar(itens, corpus_cdc))


# --- execução -------------------------------------------------------------------------


@dataclass
class RecuperadorFalso:
    """Devolve trechos combinados por pergunta. Não toca em Qdrant nem em modelo."""

    respostas: dict[str, list[list[str]]]
    nivel: Nivel = Nivel.ARTIGO

    class _R:
        nome = "falso"

    reranker = _R()

    def recuperar(self, consulta, criterios, *, k_busca=50, k_final=8):
        return [
            Trecho(
                dispositivo_id=ids[0],
                norma_urn=URN_CDC,
                rotulo_completo=ids[0],
                texto="",
                dispositivos=list(ids),
                fonte_url="",
                score=1.0,
            )
            for ids in self.respostas.get(consulta, [])[:k_final]
        ]


def golden(id: str, mecanismo: str, pergunta: str, esperados: list[str], **kw) -> ItemGolden:
    return ItemGolden(
        id=id,
        mecanismo=Mecanismo(mecanismo),
        pergunta=pergunta,
        data_referencia=date(2026, 1, 1),
        dispositivos_esperados=esperados,
        rotulos_esperados=esperados,
        **kw,
    )


def test_avaliar_conta_o_esperado_dentro_do_trecho_expandido(corpus_cdc):
    """A pergunta que o eval responde é "o dispositivo chegou ao modelo?", e com expansão
    até o artigo o inciso anotado está DENTRO do trecho, não é ele."""
    rec = RecuperadorFalso({"p": [[ART6, INC8]]})
    rel = avaliar_recuperacao(rec, [golden("t-01", "expansao", "p", [INC8])], ks=(5,))
    assert rel.recall[5] == 1.0
    assert rel.mrr == 1.0


def test_avaliar_deixa_os_itens_de_abstencao_fora_da_media(corpus_cdc):
    """Contá-los como acerto inflaria o recall com perguntas que o sistema nem deveria
    responder — mas eles continuam no relatório por item."""
    rec = RecuperadorFalso({"p": [[ART6]], "q": [[ART6]]})
    itens = [
        golden("t-01", "lexical", "p", [INC8]),
        golden("t-02", "abstencao", "q", [], deve_abster=True),
    ]
    rel = avaliar_recuperacao(rec, itens, ks=(5,))
    assert (rel.n_itens, rel.n_abstencao) == (1, 1)
    assert rel.recall[5] == 0.0, "o item medido falhou e a abstenção não pode salvá-lo"
    assert [r.id for r in rel.itens] == ["t-01", "t-02"]


def test_avaliar_usa_a_data_de_cada_item(corpus_cdc):
    """`vig-01` e `vig-02` são a mesma pergunta em datas diferentes; um único `ref` para a
    rodada apagaria metade do que o golden mede."""
    vistas = []

    class Espiao(RecuperadorFalso):
        def recuperar(self, consulta, criterios, **kw):
            vistas.append(criterios.data_referencia)
            return []

    itens = [golden("t-01", "vigencia", "p", [ART6])]
    itens[0].data_referencia = date(2010, 1, 1)
    avaliar_recuperacao(Espiao({}), itens, ks=(5,))
    assert vistas == [date(2010, 1, 1)]


def test_relatorio_quebra_por_mecanismo(corpus_cdc):
    """Um recall agregado não diz se o problema está na perna léxica, na densa ou na
    expansão — e são correções completamente diferentes."""
    rec = RecuperadorFalso({"p": [[INC8]], "q": [[ART6]]})
    itens = [
        golden("t-01", "lexical", "p", [INC8]),
        golden("t-02", "semantico", "q", [INC8]),
    ]
    rel = avaliar_recuperacao(rec, itens, ks=(5,))
    assert rel.por_mecanismo["lexical"].recall[5] == 1.0
    assert rel.por_mecanismo["semantico"].recall[5] == 0.0
    assert rel.recall[5] == 0.5


def test_relatorio_diz_o_que_faltou(corpus_cdc):
    """Um recall de 0,85 não diz o que consertar; `faltando` diz."""
    rec = RecuperadorFalso({"p": [[ART6]]})
    rel = avaliar_recuperacao(rec, [golden("t-01", "lexical", "p", [INC8])], ks=(5,))
    assert rel.itens[0].faltando == [INC8]
    assert rel.itens[0].posicao is None
