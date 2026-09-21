"""Estratégias de chunking."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.xfail(reason="esqueleto: chunking não implementado")


def test_chunk_por_folha_nao_parte_dispositivo():
    """Nenhum chunk pode cortar um dispositivo ao meio — a unidade é estrutural."""
    raise AssertionError


def test_texto_indexado_carrega_hierarquia():
    """`texto_indexado` precisa do caminho; `texto` fica cru, é ele que será citado."""
    raise AssertionError


def test_chunk_por_artigo_inclui_filhos():
    raise AssertionError
