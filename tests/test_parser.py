"""Parser estrutural, contra HTML real do Planalto em `fixtures/`.

Fixture real, não sintética: a marcação do Planalto é irregular, e um parser que só passa
em HTML limpo não sobrevive ao primeiro código de verdade.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.xfail(reason="esqueleto: parser não implementado")


def test_hierarquia_titulo_capitulo_artigo():
    raise AssertionError


def test_inciso_vira_filho_do_artigo():
    """Hierarquia correta é o que permite o parent-document retrieval funcionar."""
    raise AssertionError


def test_paragrafo_unico_e_numerado():
    raise AssertionError


def test_nota_de_alteracao_vira_vigencia():
    """"Redação dada pela Lei 9.870/1999" precisa virar data, não sobrar como texto."""
    raise AssertionError
