"""Aplicação FastAPI.

Endpoints declarados `def`, NÃO `async def`: todo o pipeline é síncrono e CPU-bound
(qdrant-client, fastembed, cross-encoder, subprocess). Declarar `async def` bloquearia o
event loop; com `def`, o FastAPI despacha no threadpool. Ver `generation/backend.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, obter_settings
from ..errors import BackendIndisponivel, RecusaDoModelo
from ..service import Consulta, Servico, construir_servico
from ..tls import confiar_no_sistema
from .deps import obter_servico
from .schemas import ConsultaRequest, RespostaOut, de_dominio

# `Annotated` em vez de `Depends` no default: é o idioma atual do FastAPI e evita
# chamada de função em argumento default (ruff B008) sem desligar a regra.
ServicoDep = Annotated[Servico, Depends(obter_servico)]

DIR_WEB: Final = Path(__file__).parent / "web"

AVISO: Final = (
    "Ferramenta de PESQUISA em legislação, não consulta jurídica. As respostas saem de "
    "trechos de lei recuperados automaticamente e precisam ser conferidas na fonte oficial."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Carrega modelos e abre conexões uma única vez, guardando em `app.state`.

    É o único lugar do processo que constrói dependências. Os modelos somam alguns GB e
    ~26 s de carga (medido na fase 7): construí-los por requisição não é lento, é
    inviável.

    `confiar_no_sistema()` aqui e não no domínio: tem efeito global sobre TLS e por isso só
    pode ser chamado na borda. Há teste que falha se um módulo de domínio o chamar.
    """
    confiar_no_sistema()
    app.state.servico = construir_servico(app.state.cfg)
    try:
        yield
    finally:
        # A conexão é somente-leitura, mas deixá-la aberta segura o arquivo no Windows e
        # atrapalha uma reingestão logo depois.
        app.state.servico.recuperador.conn.close()


def criar_app(cfg: Settings | None = None) -> FastAPI:
    """Monta a aplicação e serve `web/` como estático na raiz.

    O front é servido pela própria API para ficar na mesma origem: sem CORS, sem segundo
    dev server.
    """
    app = FastAPI(
        title="Consulta Jurídica",
        description=AVISO,
        version="0.1.0",
        lifespan=lifespan,
    )
    # Antes do lifespan rodar: `criar_app` é chamada na construção, o lifespan no startup.
    app.state.cfg = cfg or obter_settings()

    app.add_api_route(
        "/v1/consultas",
        consultar,
        methods=["POST"],
        response_model=RespostaOut,
        summary="Responde a partir da legislação vigente na data de referência",
    )
    app.add_api_route("/v1/saude", saude, methods=["GET"], summary="Estado do serviço")

    app.add_exception_handler(BackendIndisponivel, _backend_indisponivel)
    app.add_exception_handler(RecusaDoModelo, _recusa)

    # Por último: um mount em "/" casa qualquer caminho, e as rotas são testadas na ordem
    # em que foram registradas. Montar antes engoliria /v1/*.
    app.mount("/", StaticFiles(directory=DIR_WEB, html=True), name="web")
    return app


def consultar(req: ConsultaRequest, servico: ServicoDep) -> RespostaOut:
    """POST /v1/consultas."""
    resposta = servico.responder(
        Consulta(
            pergunta=req.pergunta,
            data_referencia=req.data_referencia,
            normas=tuple(req.normas),
            top_k=req.top_k,
        )
    )
    return de_dominio(resposta)


def saude(servico: ServicoDep) -> dict[str, object]:
    """Quem está de pé e com o quê.

    Existe porque a primeira consulta leva segundos e o front precisa de algo para mostrar
    antes dela — e porque uma API que não sabe dizer se os modelos carregaram é difícil de
    demonstrar. Não toca no Qdrant: o diagnóstico de índice é do CLI da ingestão.
    """
    return {
        "aviso": AVISO,
        "backend": servico.backend.nome,
        "citacoes_nativas": servico.backend.suporta_citacoes,
        "reranker": servico.recuperador.reranker.nome,
        "expansao": servico.recuperador.nivel.value,
    }


def _backend_indisponivel(request: Request, exc: Exception) -> JSONResponse:
    """503: o modelo não pôde ser chamado. Não é erro do cliente nem defeito do pipeline."""
    return JSONResponse(
        status_code=503, content={"erro": "backend_indisponivel", "detalhe": str(exc)}
    )


def _recusa(request: Request, exc: Exception) -> JSONResponse:
    """502: o modelo recusou. Visível de propósito — num corpus de legislação é defeito."""
    return JSONResponse(
        status_code=502, content={"erro": "recusa_do_modelo", "detalhe": str(exc)}
    )
