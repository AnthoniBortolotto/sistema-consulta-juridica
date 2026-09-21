"""Filtro de vigência.

Prioridade máxima entre os testes: erro aqui não levanta exceção, apenas devolve resultado
errado. Norma revogada apresentada como vigente é o pior defeito possível no domínio.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.xfail(reason="esqueleto: filtros.construir_filtro não implementado")


def test_dispositivo_vigente_aparece(data_ref):
    """Sem `revogado_em`, o dispositivo precisa passar pelo filtro.

    Caso da sentinela: `range(gt=ref)` no Qdrant exclui pontos sem o campo, então o não
    revogado sumiria se a sentinela não fosse gravada.
    """
    raise AssertionError


def test_dispositivo_revogado_antes_da_data_some(data_ref):
    raise AssertionError


def test_dispositivo_revogado_depois_da_data_aparece(data_ref):
    """Consulta sobre o direito vigente à época de um fato."""
    raise AssertionError


def test_dispositivo_ainda_nao_vigente_some(data_ref):
    """Vacatio legis: publicado, mas sem vigência na data de referência."""
    raise AssertionError
