"""Backend local via Ollama.

Nenhum teste fala com o Ollama: o transporte é o `MockTransport` do próprio `httpx`. O que
se protege é o contrato medido com o serviço rodando — o contexto fixado, o sistema no papel
de sistema, e principalmente o corte silencioso do prompt, que o Ollama não avisa.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from consulta_juridica.config import Settings
from consulta_juridica.errors import BackendIndisponivel
from consulta_juridica.generation import prompt as pr
from consulta_juridica.generation.backend import BlocoDocumento, Pedido
from consulta_juridica.generation.ollama import BackendOllama

PEDIDO = Pedido(
    sistema=pr.sistema(date(2026, 9, 21), exigir_ancoras=True),
    documentos=(
        BlocoDocumento(
            ref="D1",
            titulo="Lei 8.078/1990, Art. 49",
            contexto="fonte: https://www.planalto.gov.br/x | id: urn:x!art49",
            texto="Art. 49 O consumidor pode desistir do contrato, no prazo de 7 dias...",
            dispositivo_id="urn:x!art49",
        ),
    ),
    pergunta="posso desistir de uma compra feita pela internet?",
)


def backend_com(resposta: httpx.Response | Exception, **kw) -> tuple[BackendOllama, list]:
    """Backend com transporte falso; devolve também a lista das requisições recebidas."""
    recebidas: list[httpx.Request] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        recebidas.append(req)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    cliente = httpx.Client(base_url="http://ollama.falso", transport=httpx.MockTransport(tratar))
    return BackendOllama("qwen3.5:4b", client=cliente, **kw), recebidas


def ok(texto: str, *, lidos: int = 3127, gerados: int = 94) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": texto},
            "prompt_eval_count": lidos,
            "eval_count": gerados,
        },
    )


# --- a requisição ---------------------------------------------------------------------


def test_sistema_vai_no_papel_de_sistema():
    """Misturar sistema e conteúdo numa string só tira do modelo a precedência que ele dá à
    instrução de sistema — e é nela que mora a regra de abstenção."""
    backend, recebidas = backend_com(ok("Sim [D1]."))
    backend.gerar(PEDIDO)
    corpo = json.loads(recebidas[0].content)
    assert recebidas[0].url.path == "/api/chat"
    assert [m["role"] for m in corpo["messages"]] == ["system", "user"]
    assert corpo["messages"][0]["content"] == PEDIDO.sistema
    assert corpo["messages"][1]["content"] == pr.render_conteudo(PEDIDO)
    assert PEDIDO.sistema not in corpo["messages"][1]["content"]


def test_o_contexto_e_fixado_explicitamente():
    """O padrão do Ollama nesta máquina é 4096 tokens, e o pedido medido tem ~3100. Deixar o
    padrão é depender de o prompt nunca crescer — e quando cresce, ele corta o começo."""
    backend, recebidas = backend_com(ok("Sim."), num_ctx=16384)
    backend.gerar(PEDIDO)
    opcoes = json.loads(recebidas[0].content)["options"]
    assert opcoes["num_ctx"] == 16384
    assert opcoes["num_predict"] == PEDIDO.max_tokens


def test_deterministico():
    """Temperatura 0 é o que torna o cache honesto e uma regressão reproduzível."""
    backend, recebidas = backend_com(ok("Sim."))
    backend.gerar(PEDIDO)
    corpo = json.loads(recebidas[0].content)
    assert corpo["options"]["temperature"] == 0
    assert corpo["stream"] is False


def test_raciocinio_ligado_por_padrao():
    """Medido: sem raciocínio, o modelo de 4B recebeu a redação de 2010 do art. 6º — sem a
    palavra "transporte" — e afirmou, citando-a, que ela listava o transporte. Com
    raciocínio, acertou. O padrão rápido seria o padrão errado."""
    backend, recebidas = backend_com(ok("Sim."))
    backend.gerar(PEDIDO)
    assert json.loads(recebidas[0].content)["think"] is True


def test_raciocinio_pode_ser_desligado():
    backend, recebidas = backend_com(ok("Sim."), pensar=False)
    backend.gerar(PEDIDO)
    assert json.loads(recebidas[0].content)["think"] is False


def test_keep_alive_segura_o_modelo_na_gpu():
    """Cada recarga custa ~10 s (medido). O padrão do Ollama, 5 min, faz uma demonstração
    com perguntas espaçadas pagar isso quase toda vez."""
    backend, recebidas = backend_com(ok("Sim."), keep_alive="30m")
    backend.gerar(PEDIDO)
    assert json.loads(recebidas[0].content)["keep_alive"] == "30m"


# --- a resposta -----------------------------------------------------------------------


def test_resposta_vira_texto_ancoras_e_uso():
    backend, _ = backend_com(ok("Sim, o prazo é de 7 dias [D1].", lidos=3127, gerados=94))
    r = backend.gerar(PEDIDO)
    assert r.texto == "Sim, o prazo é de 7 dias [D1]."
    assert [c.ref_documento for c in r.citacoes] == ["D1"]
    assert (r.uso.tokens_entrada, r.uso.tokens_saida) == (3127, 94)
    assert r.modelo == "ollama:qwen3.5:4b"


def test_sem_citations_nativas():
    """Mesmo lugar do backend CLI: o eval ponta a ponta o recusa, de propósito."""
    backend, _ = backend_com(ok("x"))
    assert backend.suporta_citacoes is False
    assert backend.nome == "ollama:qwen3.5:4b"


# --- falhas ---------------------------------------------------------------------------


def test_prompt_cortado_vira_erro_e_nao_resposta():
    """O Ollama NÃO avisa quando corta: devolve `prompt_eval_count` igual ao contexto e gera
    assim mesmo, sem o sistema. Uma resposta escrita sem a instrução de só usar os trechos é
    pior que resposta nenhuma."""
    backend, _ = backend_com(ok("resposta sem instrução", lidos=4096), num_ctx=4096)
    with pytest.raises(BackendIndisponivel, match="CJ_OLLAMA_NUM_CTX"):
        backend.gerar(PEDIDO)


def test_ollama_fora_do_ar():
    backend, _ = backend_com(httpx.ConnectError("recusada"))
    with pytest.raises(BackendIndisponivel, match="docker compose up -d ollama"):
        backend.gerar(PEDIDO)


def test_modelo_nao_baixado_diz_o_comando():
    backend, _ = backend_com(httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(BackendIndisponivel, match="ollama pull qwen3.5:4b"):
        backend.gerar(PEDIDO)


def test_erro_do_servidor():
    backend, _ = backend_com(httpx.Response(500, text="CUDA out of memory"))
    with pytest.raises(BackendIndisponivel, match="CUDA out of memory"):
        backend.gerar(PEDIDO)


def test_timeout():
    backend, _ = backend_com(httpx.ReadTimeout("lento"))
    with pytest.raises(BackendIndisponivel, match="a tempo"):
        backend.gerar(PEDIDO)


# --- composição -----------------------------------------------------------------------


def test_composition_root_monta_o_backend_local_com_cache(tmp_path):
    from consulta_juridica.service import construir_backend

    cfg = Settings(
        backend_llm="ollama",
        modelo_local="qwen3.5:4b",
        ollama_num_ctx=8192,
        dir_cache_llm=tmp_path,
    )
    backend = construir_backend(cfg)
    assert backend.nome == "ollama:qwen3.5:4b+cache"
    assert backend.suporta_citacoes is False
    assert backend.interno.num_ctx == 8192


def test_modo_de_raciocinio_separa_o_cache(tmp_path):
    """O raciocínio muda a resposta — medido, é a diferença entre acertar e errar `vig-02`.
    Sem ele na identidade do cache, ligar o raciocínio depois de rodar sem ele devolveria,
    do cache, a resposta errada do modo rápido."""
    from consulta_juridica.generation.cache import BackendComCache

    rapido, _ = backend_com(ok("texto posterior como vigente"), pensar=False)
    cuidadoso, recebidas = backend_com(ok("em 2010 o art. 6º não listava o transporte"))

    BackendComCache(rapido, tmp_path).gerar(PEDIDO)
    resposta = BackendComCache(cuidadoso, tmp_path).gerar(PEDIDO)

    assert len(recebidas) == 1, "o modo cuidadoso não pode ter sido servido pelo cache"
    assert resposta.texto.startswith("em 2010")
    assert rapido.nome != cuidadoso.nome
