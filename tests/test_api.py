"""Contrato HTTP, injeção do serviço e o front estático.

Nenhum teste aqui carrega modelo: o serviço é dublê, injetado pelo
`dependency_overrides` do FastAPI. O que precisa ser protegido é o contrato — o que entra,
o que sai, e o que NÃO sai.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest
from fastapi.testclient import TestClient

from consulta_juridica.api import app as modulo_app
from consulta_juridica.api.app import consultar, criar_app, saude
from consulta_juridica.api.deps import obter_servico
from consulta_juridica.config import Settings
from consulta_juridica.errors import BackendIndisponivel, RecusaDoModelo
from consulta_juridica.models import Citacao, MotivoAbstencao, Resposta, Trecho, Uso

TRECHO = Trecho(
    dispositivo_id="urn:lex:br:federal:lei:1990-09-11;8078!art49",
    norma_urn="urn:lex:br:federal:lei:1990-09-11;8078",
    rotulo_completo="Lei 8.078/1990, Art. 49",
    texto="Art. 49 O consumidor pode desistir do contrato, no prazo de 7 dias...",
    fonte_url="https://www.planalto.gov.br/ccivil_03/leis/l8078.htm",
    score=0.98,
    score_fusao=0.5,
    score_rerank=0.98,
    dispositivos=["urn:lex:br:federal:lei:1990-09-11;8078!art49"],
)

CITACAO = Citacao(
    dispositivo_id=TRECHO.dispositivo_id,
    rotulo_completo=TRECHO.rotulo_completo,
    texto_citado="no prazo de 7 dias",
    fonte_url=TRECHO.fonte_url,
    inicio_char=40,
    fim_char=58,
)


def resposta_de(**kw) -> Resposta:
    base = dict(
        texto="Sim, o prazo é de sete dias.",
        citacoes=[CITACAO],
        trechos=[TRECHO],
        data_referencia=date(2026, 9, 21),
        modelo="claude-opus-5",
        versao_prompt="v1",
        uso=Uso(tokens_entrada=1200, tokens_saida=90),
    )
    return Resposta(**{**base, **kw})


class ServicoFalso:
    """Registra a `Consulta` recebida e devolve o que for combinado."""

    def __init__(self, resposta=None, erro: Exception | None = None):
        self.resposta = resposta if resposta is not None else resposta_de()
        self.erro = erro
        self.consultas: list = []
        self.backend = type("B", (), {"nome": "falso", "suporta_citacoes": True})()
        self.recuperador = type(
            "R",
            (),
            {
                "reranker": type("K", (), {"nome": "identidade"})(),
                "nivel": type("N", (), {"value": "artigo"})(),
            },
        )()

    def responder(self, consulta):
        self.consultas.append(consulta)
        if self.erro:
            raise self.erro
        return self.resposta


@pytest.fixture
def servico() -> ServicoFalso:
    return ServicoFalso()


@pytest.fixture
def cliente(servico):
    """Cliente com o serviço injetado. Sem `with`: o lifespan real carregaria os modelos."""
    app = criar_app(Settings())
    app.dependency_overrides[obter_servico] = lambda: servico
    return TestClient(app)


def consultar_com(cliente, **campos):
    corpo = {"pergunta": "posso desistir da compra?", "data_referencia": "2026-09-21"}
    return cliente.post("/v1/consultas", json={**corpo, **campos})


# --- contrato -------------------------------------------------------------------------


def test_consulta_devolve_resposta_citacoes_e_trechos(cliente):
    corpo = consultar_com(cliente).json()
    assert corpo["resposta"] == "Sim, o prazo é de sete dias."
    assert corpo["citacoes"] == [
        {
            "dispositivo": TRECHO.dispositivo_id,
            "rotulo": "Lei 8.078/1990, Art. 49",
            "texto_citado": "no prazo de 7 dias",
            "url": TRECHO.fonte_url,
        }
    ]
    assert corpo["modelo"] == "claude-opus-5"


def test_os_scores_chegam_ao_front(cliente):
    """O painel de trechos existe para distinguir falha de recuperação de alucinação do
    modelo. Sem os dois scores, não dá para saber qual perna falhou."""
    (trecho,) = consultar_com(cliente).json()["trechos_recuperados"]
    assert (trecho["score_fusao"], trecho["score_rerank"]) == (0.5, 0.98)
    assert trecho["rotulo"] == "Lei 8.078/1990, Art. 49"
    assert trecho["texto"] == TRECHO.texto


def test_a_resposta_nao_vaza_instrumentacao_de_eval(cliente):
    """`versao_prompt` e `uso` são instrumentação, não contrato de cliente: expô-los
    obrigaria a mantê-los para sempre."""
    corpo = consultar_com(cliente).json()
    assert "versao_prompt" not in corpo
    assert "uso" not in corpo


def test_a_consulta_chega_ao_dominio_como_foi_pedida(cliente, servico):
    consultar_com(cliente, data_referencia="2010-01-01", top_k=3, normas=["urn:x"])
    (consulta,) = servico.consultas
    assert consulta.data_referencia == date(2010, 1, 1)
    assert (consulta.top_k, consulta.normas) == (3, ("urn:x",))


def test_abstencao_aparece_no_contrato(cliente, servico):
    servico.resposta = resposta_de(
        abstencao=MotivoAbstencao.SEM_CANDIDATOS, citacoes=[], trechos=[]
    )
    assert consultar_com(cliente).json()["abstencao"] == "sem_candidatos"


# --- validação ------------------------------------------------------------------------


def test_data_de_referencia_e_obrigatoria(cliente):
    """Sem default no contrato HTTP: o cliente tem de dizer sobre qual direito vigente
    está perguntando. Um default viraria `hoje` e a consulta retroativa sumiria."""
    r = cliente.post("/v1/consultas", json={"pergunta": "qualquer coisa"})
    assert r.status_code == 422


def test_pergunta_vazia_e_recusada(cliente, servico):
    assert consultar_com(cliente, pergunta="a").status_code == 422
    assert servico.consultas == [], "não pode chegar ao domínio"


def test_top_k_fora_da_faixa_e_recusado(cliente):
    assert consultar_com(cliente, top_k=99).status_code == 422


# --- falhas do backend ----------------------------------------------------------------


def test_backend_indisponivel_vira_503(servico):
    """Não é erro do cliente nem defeito do pipeline: o modelo não pôde ser chamado."""
    servico.erro = BackendIndisponivel("claude não encontrado")
    app = criar_app(Settings())
    app.dependency_overrides[obter_servico] = lambda: servico
    r = consultar_com(TestClient(app, raise_server_exceptions=False))
    assert r.status_code == 503
    assert r.json()["erro"] == "backend_indisponivel"


def test_recusa_do_modelo_vira_502(servico):
    """Visível de propósito: num corpus de legislação, recusa é sinal de defeito."""
    servico.erro = RecusaDoModelo("categoria: None")
    app = criar_app(Settings())
    app.dependency_overrides[obter_servico] = lambda: servico
    r = consultar_com(TestClient(app, raise_server_exceptions=False))
    assert r.status_code == 502
    assert r.json()["erro"] == "recusa_do_modelo"


# --- saúde e front --------------------------------------------------------------------


def test_saude_diz_com_o_que_esta_rodando(cliente):
    corpo = cliente.get("/v1/saude").json()
    assert corpo["backend"] == "falso"
    assert corpo["citacoes_nativas"] is True
    assert "pesquisa" in corpo["aviso"].lower()


def test_a_raiz_serve_o_front(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Consulta Jurídica" in r.text


def test_o_front_e_servido_da_mesma_origem_sem_build(cliente):
    """Vue por CDN e sem bundler: o HTML precisa carregar o Vue e falar com /v1 relativo."""
    html = cliente.get("/").text
    assert "vue@3" in html
    assert "/v1/consultas" in html


def test_o_estatico_na_raiz_nao_engole_a_api(cliente):
    """Um mount em "/" casa qualquer caminho; registrado antes das rotas, mataria /v1/*."""
    assert cliente.get("/v1/saude").status_code == 200
    assert cliente.get("/nao-existe").status_code == 404


# --- regras estruturais ---------------------------------------------------------------


def test_endpoints_sao_sincronos():
    """Regra do projeto, e não estilo: o pipeline inteiro é CPU-bound e síncrono. Um
    `async def` aqui bloquearia o event loop em vez de ir para o threadpool."""
    assert not inspect.iscoroutinefunction(consultar)
    assert not inspect.iscoroutinefunction(saude)


def test_lifespan_constroi_uma_vez_e_fecha_a_conexao(monkeypatch, servico):
    """Os modelos somam alguns GB e ~26 s de carga: construí-los por requisição não é
    lento, é inviável."""
    fechou: list[bool] = []
    servico.recuperador.conn = type("C", (), {"close": lambda self: fechou.append(True)})()
    construcoes: list = []

    def construir(cfg):
        construcoes.append(cfg)
        return servico

    monkeypatch.setattr(modulo_app, "construir_servico", construir)
    monkeypatch.setattr(modulo_app, "confiar_no_sistema", lambda: None)

    app = criar_app(Settings())
    with TestClient(app) as cliente:
        consultar_com(cliente)
        consultar_com(cliente)

    assert len(construcoes) == 1
    assert fechou == [True]


def test_sem_lifespan_o_servico_vem_do_state():
    """`obter_servico` lê `app.state` e não constrói nada — é o que garante a carga única."""
    fonte = inspect.getsource(obter_servico)
    assert "app.state.servico" in fonte
    assert "construir" not in fonte
