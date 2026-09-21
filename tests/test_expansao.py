"""Expansão inciso -> artigo.

O teste que importa é o do vazamento de vigência: o filtro do Qdrant exclui o inciso
revogado, mas a expansão lê o artigo inteiro do SQLite. Sem reaplicar a data aqui, os
irmãos revogados voltam pela porta dos fundos e chegam ao modelo como direito vigente.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.xfail(reason="esqueleto: expansao.expandir não implementado")


def test_expansao_sobe_do_inciso_ao_artigo(data_ref):
    raise AssertionError


def test_expansao_omite_irmaos_revogados(data_ref):
    """O caso que motiva `data_referencia` ser obrigatório em `expandir`."""
    raise AssertionError


def test_deduplica_incisos_do_mesmo_artigo(data_ref):
    raise AssertionError
