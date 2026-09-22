"""Aquisição e procedência dos documentos brutos.

Sem rede: o que importa testar aqui é o que acontece DEPOIS do download — a procedência
gravada, a conferência do hash e a detecção de encoding. Baixar de verdade é teste da
rede do Planalto, não deste código.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from consulta_juridica.ingest.fontes import (
    ENCODING_PLANALTO,
    USER_AGENT,
    DocumentoBruto,
    FalhaDownload,
    FontePlanalto,
    carregar,
    iter_brutos,
    salvar,
    sha256_de,
)
from consulta_juridica.urn import CORPUS

URN = "urn:lex:br:federal:lei:1990-09-11;8078"


def doc(conteudo: bytes) -> DocumentoBruto:
    return DocumentoBruto(
        urn=URN,
        url="https://www.planalto.gov.br/ccivil_03/leis/l8078.htm",
        conteudo=conteudo,
        content_type="text/html",
        sha256=sha256_de(conteudo),
        baixado_em=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )


def test_user_agent_de_navegador():
    """Sem UA de browser o Planalto devolve zero byte e estoura o timeout."""
    assert "Mozilla" in USER_AGENT and "Chrome" in USER_AGENT


def test_fonte_planalto_lista_o_corpus_fechado():
    """Uma lista de normas só: a mesma que o golden usa para resolver apelido."""
    refs = list(FontePlanalto().listar())
    assert {r.urn for r in refs} == {n.urn for n in CORPUS}
    assert all(r.url.startswith("https://www.planalto.gov.br/") for r in refs)


def test_salvar_grava_procedencia(tmp_path: Path):
    caminho = salvar(doc(b"<html>ola</html>"), tmp_path)
    meta = caminho.with_suffix(caminho.suffix + ".meta.json")
    assert caminho.read_bytes() == b"<html>ola</html>"
    assert meta.exists() and "sha256" in meta.read_text(encoding="utf-8")


def test_carregar_devolve_o_mesmo_documento(tmp_path: Path):
    """Reparsear sem rede é o caminho que mais roda enquanto o parser amadurece."""
    original = doc("<html>ação</html>".encode(ENCODING_PLANALTO))
    lido = carregar(salvar(original, tmp_path))
    assert (lido.urn, lido.sha256, lido.conteudo) == (
        original.urn,
        original.sha256,
        original.conteudo,
    )
    assert lido.baixado_em == original.baixado_em


def test_carregar_recusa_bruto_corrompido(tmp_path: Path):
    """Bruto corrompido produziria árvore truncada em silêncio, e `sha256_origem` mentiria."""
    caminho = salvar(doc(b"<html>original</html>"), tmp_path)
    caminho.write_bytes(b"<html>adulterado</html>")
    with pytest.raises(FalhaDownload, match="sha256"):
        carregar(caminho)


def test_carregar_exige_procedencia(tmp_path: Path):
    solto = tmp_path / "sem-meta.htm"
    solto.write_bytes(b"<html/>")
    with pytest.raises(FalhaDownload, match="procedência"):
        carregar(solto)


def test_iter_brutos_percorre_o_diretorio(tmp_path: Path):
    salvar(doc(b"<html>a</html>"), tmp_path)
    assert [d.urn for d in iter_brutos(tmp_path)] == [URN]


# --- encoding -------------------------------------------------------------------------


def test_detecta_cp1252_da_fonte_real():
    """O Planalto não declara charset e não é utf-8; decodificar como utf-8 estoura."""
    assert doc("São direitos".encode(ENCODING_PLANALTO)).texto() == "São direitos"


def test_detecta_utf8():
    """A fixture do repositório foi salva em utf-8 — os dois casos convivem."""
    assert doc("São direitos".encode()).texto() == "São direitos"


def test_encoding_explicito_tem_precedencia():
    assert doc("São".encode(ENCODING_PLANALTO)).texto(ENCODING_PLANALTO) == "São"


def test_corpus_real_e_cp1252():
    """Prova contra o arquivo baixado, não contra uma string de teste.

    Pula quando `data/raw` está vazio: o diretório é ignorado pelo git, então num clone
    novo ele não existe. Teste que exige corpus baixado para passar seria um teste que
    falha por motivo errado.
    """
    from consulta_juridica.config import Settings
    from consulta_juridica.ingest.fontes import carregar, listar_brutos

    brutos = listar_brutos(Settings().dir_raw)
    if not brutos:
        pytest.skip("sem corpus em data/raw — `python -m consulta_juridica.ingest baixar`")

    bruto = carregar(brutos[0]).conteudo
    with pytest.raises(UnicodeDecodeError):
        bruto.decode("utf-8")
    assert "ç" in doc(bruto).texto(), "a detecção precisa recuperar os acentos"
