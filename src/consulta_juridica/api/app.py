"""Aplicação FastAPI.

Endpoints declarados `def`, NÃO `async def`: todo o pipeline é síncrono e CPU-bound
(qdrant-client, fastembed, cross-encoder, subprocess). Declarar `async def` bloquearia o
event loop; com `def`, o FastAPI despacha no threadpool. Ver `generation/backend.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from ..config import Settings
from ..service import Servico
from .deps import obter_servico
from .schemas import ConsultaRequest, RespostaOut


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Carrega modelos e abre conexões uma única vez, guardando em `app.state`."""
    raise NotImplementedError
    yield  # pragma: no cover


def criar_app(cfg: Settings | None = None) -> FastAPI:
    """Monta a aplicação e serve `web/` como estático na raiz.

    O front é servido pela própria API para ficar na mesma origem: sem CORS, sem segundo
    dev server.
    """
    raise NotImplementedError


def consultar(
    req: ConsultaRequest, servico: Servico = Depends(obter_servico)
) -> RespostaOut:
    """POST /v1/consultas."""
    raise NotImplementedError
