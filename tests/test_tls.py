"""Confiança TLS.

O que precisa ser garantido aqui não é que o download funcione — isso depende da máquina
— mas que o projeto **nunca** desligue a verificação. O `sha256` de procedência que
`fontes.salvar` grava só significa alguma coisa se os bytes vieram comprovadamente da
fonte; `verify=False` transformaria essa garantia em teatro.
"""

from __future__ import annotations

import ssl
from pathlib import Path

from consulta_juridica import tls

RAIZ = Path(__file__).parents[1] / "src" / "consulta_juridica"


def test_contexto_verifica_o_certificado():
    ctx = tls.contexto()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_contexto_carrega_raizes():
    """Contexto sem nenhuma CA carregada rejeitaria tudo — e o sintoma seria "a rede caiu"."""
    assert tls.contexto().cert_store_stats()["x509_ca"] > 0


def test_confiar_no_sistema_nunca_levanta():
    """Numa máquina sem inspeção de TLS o certifi já basta; falhar aqui seria pior."""
    assert isinstance(tls.confiar_no_sistema(), bool)


def test_nenhum_modulo_desliga_a_verificacao():
    """`verify=False` em qualquer lugar do projeto anula a procedência do corpus."""
    proibido = ("verify=False", "verify = False", "CERT_NONE", "check_hostname = False")
    achados = [
        f"{py.relative_to(RAIZ)}: {termo}"
        for py in RAIZ.rglob("*.py")
        for termo in proibido
        if termo in py.read_text(encoding="utf-8")
    ]
    assert not achados, achados


def test_injecao_so_acontece_na_borda():
    """`inject_into_ssl` tem efeito global: só a borda pode chamá-la.

    Módulo de domínio que injetasse no import mudaria o comportamento TLS de quem
    apenas importa o pacote — inclusive de um processo que nem usa a ingestão.
    """
    bordas = {"tls.py", "__main__.py", "app.py"}
    vazamentos = [
        str(py.relative_to(RAIZ))
        for py in RAIZ.rglob("*.py")
        if "confiar_no_sistema()" in py.read_text(encoding="utf-8") and py.name not in bordas
    ]
    assert not vazamentos, vazamentos
