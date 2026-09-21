"""Fixtures compartilhadas."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

DIR_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data_ref() -> date:
    """Data de referência fixa. Nunca `date.today()` em teste — o resultado mudaria sozinho."""
    return date(2026, 1, 1)


@pytest.fixture
def conn():
    """SQLite em memória com o schema aplicado."""
    pytest.skip("store.db.conectar ainda não implementado")
