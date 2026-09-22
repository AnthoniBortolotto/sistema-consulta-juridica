"""Prompt, backends, cache e resolução de citação.

**Nenhum teste aqui chama o Claude.** O que precisa ser protegido é o contrato: a forma da
requisição (blocos `document` com citations em todos), a leitura da resposta, e a tradução
de deslocamento de caractere para dispositivo — que é onde uma citação vira verificável ou
vira mentira bem formatada.

Os trechos são construídos pela expansão de verdade, contra o SQLite em memória. Fabricar
um `Trecho` à mão esconderia exatamente o que os testes de citação precisam exercitar: o
layout de linhas que `retrieval.expansao` produz.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from consulta_juridica.errors import BackendIndisponivel, RecusaDoModelo
from consulta_juridica.generation import citacoes as cit
from consulta_juridica.generation import prompt as pr
from consulta_juridica.generation.backend import (
    BlocoDocumento,
    CitacaoBruta,
    LLMBackend,
    Pedido,
    ResultadoGeracao,
)
from consulta_juridica.generation.cache import BackendComCache
from consulta_juridica.generation.claude_api import BackendMessagesAPI
from consulta_juridica.generation.claude_cli import BackendClaudeCLI
from consulta_juridica.models import MotivoAbstencao, Uso
from consulta_juridica.retrieval.expansao import MARCA_OMISSAO, Nivel, expandir
from consulta_juridica.service import Consulta, Servico

from .conftest import candidatos_de, id_cdc

ART6 = id_cdc("tit1", "cap3", "art6")
INC2 = id_cdc("tit1", "cap3", "art6", "inc2")
INC8 = id_cdc("tit1", "cap3", "art6", "inc8")


@pytest.fixture
def trecho(corpus_cdc, arvore_cdc, norma_cdc, data_ref):
    """Art. 6º do CDC expandido: caput mais três incisos vigentes, em linhas."""
    (t,) = expandir(
        corpus_cdc,
        candidatos_de(arvore_cdc, norma_cdc, [INC8]),
        nivel=Nivel.ARTIGO,
        data_referencia=data_ref,
    )
    return t


@pytest.fixture
def trecho_cortado(corpus_cdc, arvore_cdc, norma_cdc, data_ref):
    """O mesmo artigo com corte, para exercitar a marca de omissão."""
    (t,) = expandir(
        corpus_cdc,
        candidatos_de(arvore_cdc, norma_cdc, [INC8]),
        nivel=Nivel.ARTIGO,
        data_referencia=data_ref,
        max_chars=120,
    )
    return t


@pytest.fixture
def pedido(trecho, data_ref) -> Pedido:
    return pr.montar_pedido(
        "o consumidor tem direito à inversão do ônus da prova?",
        [trecho],
        data_ref,
        suporta_citacoes=True,
    )


# --- prompt ---------------------------------------------------------------------------


def test_sistema_fixa_a_data_de_referencia(data_ref):
    """Os trechos já vêm filtrados por vigência; o modelo precisa saber disso para não
    ressalvar que 'a lei pode ter mudado' — a ressalva já foi aplicada."""
    texto = pr.sistema(data_ref, exigir_ancoras=False)
    assert data_ref.isoformat() in texto
    assert "filtrados por vigência" in texto


def test_sistema_ensina_a_marca_de_abstencao(data_ref):
    assert pr.MARCA_ABSTENCAO in pr.sistema(data_ref, exigir_ancoras=False)


def test_ancoras_so_quando_o_backend_precisa(data_ref):
    """Instrução de âncora no backend com citations nativas seria ruído: a API já devolve a
    citação estruturada, e pedir marcação no texto só polui a resposta."""
    assert "[D1]" not in pr.sistema(data_ref, exigir_ancoras=False)
    assert "[D1]" in pr.sistema(data_ref, exigir_ancoras=True)


def test_montar_pedido_inverte_citacoes_em_ancoras(trecho, data_ref):
    com = pr.montar_pedido("p", [trecho], data_ref, suporta_citacoes=True)
    sem = pr.montar_pedido("p", [trecho], data_ref, suporta_citacoes=False)
    assert "[D1]" not in com.sistema
    assert "[D1]" in sem.sistema


def test_o_corpo_do_documento_e_o_trecho_cru(trecho, data_ref):
    """A invariante da fase. As citations devolvem deslocamento DENTRO do corpo enviado;
    acrescentar um rótulo ou uma URL ao corpo desloca todos eles, e cada citação passa a
    apontar para o dispositivo errado — sem erro, sem sintoma."""
    (doc,) = pr.montar_documentos([trecho])
    assert doc.texto == trecho.texto
    assert trecho.rotulo_completo not in doc.texto
    assert doc.titulo == trecho.rotulo_completo
    assert trecho.fonte_url in doc.contexto


def test_refs_sao_estaveis_e_numeradas(trecho, trecho_cortado):
    docs = pr.montar_documentos([trecho, trecho_cortado])
    assert [d.ref for d in docs] == ["D1", "D2"]


def test_render_texto_unico_leva_tudo(pedido):
    """O backend CLI recebe uma string só: se algo não entrar aqui, o modelo não vê."""
    texto = pr.render_texto_unico(pedido)
    for d in pedido.documentos:
        assert d.texto in texto
        assert d.ref in texto
    assert pedido.pergunta in texto
    assert pedido.sistema in texto


# --- chave do pedido ------------------------------------------------------------------


def test_chave_e_estavel_e_sensivel(pedido, trecho, data_ref):
    igual = pr.montar_pedido(pedido.pergunta, [trecho], data_ref, suporta_citacoes=True)
    outra = pr.montar_pedido("outra pergunta", [trecho], data_ref, suporta_citacoes=True)
    outra_data = pr.montar_pedido(
        pedido.pergunta, [trecho], date(2019, 1, 1), suporta_citacoes=True
    )
    assert pedido.chave() == igual.chave()
    assert pedido.chave() != outra.chave()
    assert pedido.chave() != outra_data.chave(), "a data está no sistema e tem de contar"


def test_chave_muda_quando_o_contexto_muda(pedido, trecho, trecho_cortado, data_ref):
    """É o que faz o eval re-cobrar só o que mudou: mexer na recuperação muda o texto dos
    trechos de algumas perguntas e não de outras."""
    outro = pr.montar_pedido(
        pedido.pergunta, [trecho_cortado], data_ref, suporta_citacoes=True
    )
    assert pedido.chave() != outro.chave()


# --- cache ----------------------------------------------------------------------------


class BackendContador(LLMBackend):
    nome = "contador"
    suporta_citacoes = True

    def __init__(self, resultado: ResultadoGeracao | None = None) -> None:
        self.chamadas = 0
        self.resultado = resultado or ResultadoGeracao(
            texto="resposta",
            citacoes=[CitacaoBruta("D1", "texto citado", 3, 10)],
            modelo="modelo-x",
            uso=Uso(tokens_entrada=100, tokens_saida=20, tokens_cache_leitura=5),
        )

    def gerar(self, pedido: Pedido) -> ResultadoGeracao:
        self.chamadas += 1
        return self.resultado


def test_cache_evita_a_segunda_chamada(pedido, tmp_path):
    interno = BackendContador()
    backend = BackendComCache(interno, tmp_path)
    primeiro = backend.gerar(pedido)
    segundo = backend.gerar(pedido)
    assert interno.chamadas == 1
    assert (segundo.texto, segundo.modelo) == (primeiro.texto, primeiro.modelo)


def test_cache_preserva_citacoes_e_uso(pedido, tmp_path):
    interno = BackendContador()
    BackendComCache(interno, tmp_path).gerar(pedido)
    lido = BackendComCache(interno, tmp_path).gerar(pedido)
    assert lido.citacoes == [CitacaoBruta("D1", "texto citado", 3, 10)]
    assert lido.uso.tokens_entrada == 100
    assert interno.chamadas == 1


def test_cache_separa_backends(pedido, tmp_path):
    """Duas respostas de modelos diferentes ao mesmo pedido não podem compartilhar entrada:
    o eval compararia um modelo com o cache do outro sem perceber."""
    a, b = BackendContador(), BackendContador()
    a.nome, b.nome = "messages-api:claude-opus-5", "messages-api:claude-haiku-4-5"
    BackendComCache(a, tmp_path).gerar(pedido)
    BackendComCache(b, tmp_path).gerar(pedido)
    assert (a.chamadas, b.chamadas) == (1, 1)


def test_cache_nao_deixa_arquivo_parcial(pedido, tmp_path):
    BackendComCache(BackendContador(), tmp_path).gerar(pedido)
    assert list(tmp_path.rglob("*.parcial")) == []
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_cache_desligado_e_so_nao_envolver(pedido, tmp_path):
    """Sem o decorator não há caminho de cache nenhum — nada de flag interna."""
    interno = BackendContador()
    interno.gerar(pedido)
    interno.gerar(pedido)
    assert interno.chamadas == 2


# --- resolução de citação -------------------------------------------------------------


def faixa_de(trecho, dispositivo_id) -> tuple[int, int]:
    return next((i, f) for i, f, d in cit.faixas(trecho) if d == dispositivo_id)


def test_faixas_pulam_a_marca_de_omissao(trecho_cortado):
    assert MARCA_OMISSAO in trecho_cortado.texto
    faixas = cit.faixas(trecho_cortado)
    assert [d for _, _, d in faixas] == trecho_cortado.dispositivos
    for ini, fim, _ in faixas:
        assert trecho_cortado.texto[ini:fim] != MARCA_OMISSAO


def test_citacao_aponta_para_o_inciso_e_nao_para_o_artigo(trecho, data_ref):
    """O trecho enviado é o artigo inteiro; a citação cai dentro de um inciso. Devolver o
    artigo seria perder justamente a precisão pela qual a expansão paga."""
    ini, fim = faixa_de(trecho, INC8)
    docs = pr.montar_documentos([trecho])
    bruta = CitacaoBruta("D1", trecho.texto[ini:fim], ini, fim)
    (resolvida,) = cit.resolver([bruta], docs, [trecho])
    assert resolvida.dispositivo_id == INC8
    assert resolvida.fonte_url == trecho.fonte_url


def test_citacao_a_cavalo_fica_com_quem_tem_mais_texto(trecho):
    """A API divide por sentença e o caput termina em dois-pontos, com o inciso completando
    a frase — uma citação atravessar a fronteira é normal, não é caso de canto."""
    ini_caput, fim_caput = faixa_de(trecho, ART6)
    _, fim_inc2 = faixa_de(trecho, INC2)
    ini = fim_caput - 5
    docs = pr.montar_documentos([trecho])
    bruta = CitacaoBruta("D1", trecho.texto[ini:fim_inc2], ini, fim_inc2)
    (resolvida,) = cit.resolver([bruta], docs, [trecho])
    assert resolvida.dispositivo_id == INC2
    assert ini_caput < ini


def test_citacao_com_texto_que_nao_bate_e_descartada(trecho):
    """Só falha se o corpo enviado divergir de `Trecho.texto` — e aí o defeito não é desta
    citação, é de todas, porque todos os deslocamentos estão errados."""
    ini, fim = faixa_de(trecho, INC8)
    docs = pr.montar_documentos([trecho])
    assert cit.resolver([CitacaoBruta("D1", "texto inventado", ini, fim)], docs, [trecho]) == []


def test_citacao_de_documento_inexistente_e_descartada(trecho):
    docs = pr.montar_documentos([trecho])
    assert cit.resolver([CitacaoBruta("D9", "x", 0, 1)], docs, [trecho]) == []


def test_citacao_so_da_marca_de_omissao_e_descartada(trecho_cortado):
    """Citar `[…]` seria citar texto que não é lei."""
    ini = trecho_cortado.texto.index(MARCA_OMISSAO)
    fim = ini + len(MARCA_OMISSAO)
    docs = pr.montar_documentos([trecho_cortado])
    bruta = CitacaoBruta("D1", MARCA_OMISSAO, ini, fim)
    assert cit.resolver([bruta], docs, [trecho_cortado]) == []


def test_ancora_sem_deslocamento_aponta_para_o_trecho(trecho):
    """O que o backend CLI consegue: o dispositivo, sem o intervalo. É a perda concreta de
    não usar a Messages API."""
    docs = pr.montar_documentos([trecho])
    (resolvida,) = cit.resolver(cit.extrair_ancoras("porque sim [D1]."), docs, [trecho])
    assert resolvida.dispositivo_id == trecho.dispositivo_id
    assert (resolvida.inicio_char, resolvida.texto_citado) == (None, "")


def test_extrair_ancoras_nao_repete(trecho):
    assert len(cit.extrair_ancoras("a [D1] b [D1] c [D2]")) == 2


def test_detectar_abstencao():
    assert cit.detectar_abstencao(f"{pr.MARCA_ABSTENCAO}\nfaltou o CPC.")
    assert cit.detectar_abstencao(f"  {pr.MARCA_ABSTENCAO.lower()} porque...")
    assert not cit.detectar_abstencao("O art. 6º, VIII do CDC prevê a inversão.")


# --- backend da Messages API ----------------------------------------------------------


class Bloco:
    def __init__(self, texto, citacoes=None):
        self.type, self.text, self.citations = "text", texto, citacoes or []


class CitacaoAPI:
    """A forma documentada de `char_location` para documento de texto puro."""

    def __init__(self, indice, texto, inicio, fim):
        self.type = "char_location"
        self.document_index, self.cited_text = indice, texto
        self.start_char_index, self.end_char_index = inicio, fim


class UsoAPI:
    input_tokens, output_tokens, cache_read_input_tokens = 1200, 300, 800


class RespostaAPI:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content, self.stop_reason, self.stop_details = content, stop_reason, stop_details
        self.model, self.usage = "claude-opus-5", UsoAPI()


class ClienteFalso:
    """Captura a requisição e devolve uma resposta canned. Não fala com a rede."""

    def __init__(self, resposta):
        self.resposta, self.requisicao = resposta, None
        self.messages = self

    def create(self, **kwargs):
        self.requisicao = kwargs
        return self.resposta


def backend_com(resposta) -> tuple[BackendMessagesAPI, ClienteFalso]:
    cliente = ClienteFalso(resposta)
    return BackendMessagesAPI("claude-opus-5", client=cliente), cliente


def test_requisicao_manda_documento_de_texto_com_citations(pedido):
    backend, cliente = backend_com(RespostaAPI([Bloco("ok")]))
    backend.gerar(pedido)
    conteudo = cliente.requisicao["messages"][0]["content"]
    docs = [b for b in conteudo if b["type"] == "document"]
    assert len(docs) == len(pedido.documentos)
    for bloco, original in zip(docs, pedido.documentos, strict=True):
        assert bloco["source"] == {
            "type": "text",
            "media_type": "text/plain",
            "data": original.texto,
        }
        # Citations têm de estar em TODOS os documentos ou em nenhum: a API rejeita mistura.
        assert bloco["citations"] == {"enabled": True}
        assert bloco["title"] == original.titulo


def test_a_pergunta_vem_depois_dos_documentos(pedido):
    backend, cliente = backend_com(RespostaAPI([Bloco("ok")]))
    backend.gerar(pedido)
    conteudo = cliente.requisicao["messages"][0]["content"]
    assert [b["type"] for b in conteudo] == ["document", "text"]
    assert conteudo[-1]["text"] == pedido.pergunta


def test_sistema_vai_com_cache_e_sem_structured_output(pedido):
    """Structured outputs e citations são incompatíveis: juntos, a API devolve 400."""
    backend, cliente = backend_com(RespostaAPI([Bloco("ok")]))
    backend.gerar(pedido)
    (sistema,) = cliente.requisicao["system"]
    assert sistema["cache_control"] == {"type": "ephemeral"}
    assert "output_config" not in cliente.requisicao
    assert "output_format" not in cliente.requisicao
    assert cliente.requisicao["max_tokens"] == pedido.max_tokens


def test_resposta_particionada_vira_texto_e_citacoes(pedido, trecho):
    """A resposta volta em vários blocos de texto, e só os apoiados em documento carregam
    citação. O texto final é a concatenação."""
    ini, fim = faixa_de(trecho, INC8)
    resposta = RespostaAPI(
        [
            Bloco("Sim. "),
            Bloco("A inversão é direito básico", [CitacaoAPI(0, trecho.texto[ini:fim], ini, fim)]),
        ]
    )
    backend, _ = backend_com(resposta)
    resultado = backend.gerar(pedido)
    assert resultado.texto == "Sim. A inversão é direito básico"
    assert resultado.citacoes == [CitacaoBruta("D1", trecho.texto[ini:fim], ini, fim)]
    assert resultado.modelo == "claude-opus-5"
    assert (resultado.uso.tokens_entrada, resultado.uso.tokens_cache_leitura) == (1200, 800)


def test_citacao_para_documento_fora_da_faixa_e_ignorada(pedido):
    resposta = RespostaAPI([Bloco("x", [CitacaoAPI(7, "t", 0, 1)])])
    backend, _ = backend_com(resposta)
    assert backend.gerar(pedido).citacoes == []


def test_recusa_do_modelo_levanta(pedido):
    """Contornar a recusa trocando de modelo faria `Resposta.modelo` mentir sobre quem
    respondeu. Num corpus de legislação, recusa é sinal de defeito."""
    backend, _ = backend_com(RespostaAPI([], stop_reason="refusal"))
    with pytest.raises(RecusaDoModelo):
        backend.gerar(pedido)


def test_o_nome_do_backend_identifica_o_modelo():
    backend, _ = backend_com(RespostaAPI([Bloco("ok")]))
    assert backend.nome == "messages-api:claude-opus-5"


# --- backend do CLI -------------------------------------------------------------------


#: Referência capturada ANTES de qualquer monkeypatch: o backend importa o módulo
#: `subprocess`, então substituir `subprocess.run` substitui para todo mundo — inclusive
#: para o dublê, que entraria em recursão infinita chamando a si mesmo.
_RUN_REAL = subprocess.run


def falso_claude(monkeypatch, tmp_path: Path, corpo: str) -> list:
    """Põe um script Python no lugar do binário `claude`.

    `claude` não está instalado nesta máquina, e o dublê exercita o subprocess de verdade:
    argumentos, encoding, stdout, código de saída. O que fica sem cobertura é só se o
    `claude` real aceita `-p` — e isso nenhum teste local pode responder.
    """
    script = tmp_path / "falso_claude.py"
    # O `claude` real escreve utf-8; o console do Windows não é utf-8 por padrão, então o
    # dublê precisa dizer isso explicitamente para reproduzir o comportamento certo.
    preambulo = "import sys\nsys.stdout.reconfigure(encoding='utf-8')\n"
    script.write_text(preambulo + corpo, encoding="utf-8")
    recebidos: list = []

    def executar(argv, **kw):
        recebidos.append(argv)
        return _RUN_REAL([sys.executable, str(script)], **kw)

    monkeypatch.setattr(subprocess, "run", executar)
    return recebidos


def test_cli_le_a_saida_e_extrai_ancoras(pedido, tmp_path, monkeypatch):
    saida = "Sim, o CDC prevê a inversão [D1]."
    falso_claude(monkeypatch, tmp_path, f"print({saida!r})")

    resultado = BackendClaudeCLI().gerar(pedido)

    assert resultado.texto == saida
    assert [c.ref_documento for c in resultado.citacoes] == ["D1"]
    assert resultado.uso.tokens_entrada == 0, "o CLI não reporta tokens"
    assert resultado.modelo.startswith("claude-cli"), "não inventar qual modelo respondeu"


def test_cli_manda_o_prompt_inteiro_em_modo_nao_interativo(pedido, tmp_path, monkeypatch):
    recebidos = falso_claude(monkeypatch, tmp_path, "print('ok')")
    BackendClaudeCLI(executavel="claude").gerar(pedido)
    (argv,) = recebidos
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == pr.render_texto_unico(pedido)


def test_cli_ausente_e_erro_claro(pedido):
    backend = BackendClaudeCLI(executavel="claude-que-nao-existe-12345")
    with pytest.raises(BackendIndisponivel, match="não encontrado"):
        backend.gerar(pedido)


def test_cli_com_saida_de_erro_levanta(pedido, tmp_path, monkeypatch):
    falso_claude(
        monkeypatch, tmp_path, "import sys; sys.stderr.write('estourou'); sys.exit(3)"
    )
    with pytest.raises(BackendIndisponivel, match="estourou"):
        BackendClaudeCLI().gerar(pedido)


def test_cli_nao_suporta_citacoes():
    """O vazamento é intencional e visível: o eval ponta a ponta recusa este backend."""
    assert BackendClaudeCLI.suporta_citacoes is False


# --- serviço --------------------------------------------------------------------------


class RecuperadorFalso:
    def __init__(self, trechos):
        self.trechos, self.chamadas = trechos, []

    def recuperar(self, consulta, criterios, **kw):
        self.chamadas.append(criterios.data_referencia)
        return self.trechos


def servico_com(trechos, backend) -> Servico:
    return Servico(recuperador=RecuperadorFalso(trechos), backend=backend, modelo="m")


def test_sem_candidatos_nao_chama_o_modelo(data_ref):
    """O caminho que não custa nada. Chamar o modelo para ele dizer que não recebeu trecho
    nenhum seria gastar token para não acrescentar informação."""
    backend = BackendContador()
    svc = servico_com([], backend)
    resposta = svc.responder(Consulta(pergunta="qualquer", data_referencia=data_ref))
    assert resposta.abstencao is MotivoAbstencao.SEM_CANDIDATOS
    assert backend.chamadas == 0
    assert resposta.uso.tokens_entrada == 0


def test_abstencao_do_modelo_e_um_motivo_diferente(trecho, data_ref):
    """`SEM_CANDIDATOS` é a busca falhando; `CONTEXTO_INSUFICIENTE` é o modelo lendo os
    trechos e dizendo que não bastam — que é o comportamento desejado em `abs-01`."""
    backend = BackendContador(
        ResultadoGeracao(
            texto=f"{pr.MARCA_ABSTENCAO}\nos trechos tratam de outro prazo.",
            citacoes=[],
            modelo="m",
            uso=Uso(),
        )
    )
    resposta = servico_com([trecho], backend).responder(
        Consulta(pergunta="p", data_referencia=data_ref)
    )
    assert resposta.abstencao is MotivoAbstencao.CONTEXTO_INSUFICIENTE
    assert backend.chamadas == 1


def test_resposta_carrega_citacao_resolvida_e_procedencia(trecho, data_ref):
    ini, fim = faixa_de(trecho, INC8)
    backend = BackendContador(
        ResultadoGeracao(
            texto="Sim.",
            citacoes=[CitacaoBruta("D1", trecho.texto[ini:fim], ini, fim)],
            modelo="claude-opus-5",
            uso=Uso(tokens_entrada=10),
        )
    )
    resposta = servico_com([trecho], backend).responder(
        Consulta(pergunta="p", data_referencia=data_ref)
    )
    assert resposta.abstencao is None
    assert [c.dispositivo_id for c in resposta.citacoes] == [INC8]
    assert resposta.versao_prompt == pr.VERSAO_PROMPT
    assert resposta.modelo == "claude-opus-5"
    assert resposta.trechos == [trecho]


def test_a_data_da_consulta_chega_a_recuperacao(trecho):
    svc = servico_com([trecho], BackendContador())
    svc.responder(Consulta(pergunta="p", data_referencia=date(2010, 1, 1)))
    assert svc.recuperador.chamadas == [date(2010, 1, 1)]


def test_documento_e_trecho_nao_se_desalinham(trecho, data_ref):
    """O acoplamento que sustenta a citação: `resolver` casa documento com trecho pelo
    `dispositivo_id`, então os dois lados precisam concordar."""
    docs = pr.montar_documentos([trecho])
    assert [d.dispositivo_id for d in docs] == [trecho.dispositivo_id]
    assert isinstance(docs[0], BlocoDocumento)
