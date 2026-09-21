"""Dependências do FastAPI."""

from __future__ import annotations

from fastapi import Request

from ..config import Settings
from ..service import Servico


def obter_servico(request: Request) -> Servico:
    """Devolve o serviço criado no lifespan e guardado em `app.state`.

    Nunca construa o serviço aqui: bge-m3 e o cross-encoder somam alguns GB e segundos de
    carga, e recarregá-los a cada requisição inviabiliza a API.
    """
    raise NotImplementedError


def obter_config(request: Request) -> Settings:
    raise NotImplementedError
