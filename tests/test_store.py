"""Camada de store: conexão, escrita e leitura do SQLite.

O teste que mais importa aqui é `test_subarvore_omite_revogado`: é a fonte da verdade do
vazamento de vigência descrito em `queries.subarvore`. O filtro do Qdrant não protege
este caminho.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from consulta_juridica.models import Norma, Remissao, TipoDispositivo
from consulta_juridica.store import db, queries, writer
from consulta_juridica.urn import montar_urn_dispositivo

from .conftest import URN_CDC, URN_CF, disp

ART6 = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6")
INC8 = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6/inc8")
INC4_REVOGADO = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6/inc4")
INC11_FUTURO = montar_urn_dispositivo(URN_CDC, "tit1/cap3/art6/inc11")


# --- db -------------------------------------------------------------------------------


def test_schema_e_idempotente(conn):
    db.aplicar_schema(conn)
    db.aplicar_schema(conn)
    tabelas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"norma", "dispositivo", "remissao", "ingestao"} <= tabelas


def test_foreign_keys_ligadas(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transacao_faz_rollback(conn, corpus_cdc):
    antes = conn.execute("SELECT COUNT(*) FROM dispositivo").fetchone()[0]
    with pytest.raises(RuntimeError), db.transacao(conn):
        conn.execute("DELETE FROM dispositivo")
        raise RuntimeError("falha no meio da ingestão")
    assert conn.execute("SELECT COUNT(*) FROM dispositivo").fetchone()[0] == antes


def test_somente_leitura_recusa_escrita(tmp_path: Path):
    caminho = tmp_path / "db" / "corpus.sqlite3"
    escrita = db.conectar(caminho)
    db.aplicar_schema(escrita)
    escrita.close()

    leitura = db.conectar(caminho, somente_leitura=True)
    with pytest.raises(sqlite3.OperationalError):
        leitura.execute("DELETE FROM dispositivo")
    leitura.close()


def test_somente_leitura_recusa_banco_inexistente(tmp_path: Path):
    """Consulta contra corpus que não existe é erro, não zero resultados."""
    with pytest.raises(sqlite3.OperationalError):
        db.conectar(tmp_path / "nao-existe.sqlite3", somente_leitura=True)


# --- writer ---------------------------------------------------------------------------


def test_upsert_norma_atualiza_em_vez_de_duplicar(conn):
    norma = Norma(
        urn=URN_CF, tipo="constituicao", ano=1988, fonte_url="http://x", sha256_origem="a" * 64
    )
    with db.transacao(conn):
        writer.upsert_norma(conn, norma)
        writer.upsert_norma(conn, norma.model_copy(update={"sha256_origem": "b" * 64}))
    assert conn.execute("SELECT COUNT(*) FROM norma").fetchone()[0] == 1
    assert queries.obter_norma(conn, URN_CF).sha256_origem == "b" * 64


def test_substituir_insere_pai_antes_do_filho(conn):
    """A FK auto-referente rejeita filho órfão, e a entrada não vem ordenada.

    Aqui o filho é passado ANTES do pai de propósito: quem ordena é
    `substituir_dispositivos`, pela profundidade do materialized path.
    """
    T = TipoDispositivo
    with db.transacao(conn):
        writer.upsert_norma(
            conn,
            Norma(
                urn=URN_CDC, tipo="lei", numero="8078", ano=1990,
                fonte_url="http://x", sha256_origem="a" * 64,
            ),
        )
        gravados = writer.substituir_dispositivos(
            conn,
            URN_CDC,
            [
                disp(URN_CDC, ["tit1", "art1", "inc1"], T.INCISO, "I", "filho",
                     parent=["tit1", "art1"]),
                disp(URN_CDC, ["tit1", "art1"], T.ARTIGO, "Art. 1º", "pai", parent=["tit1"]),
                disp(URN_CDC, ["tit1"], T.TITULO, "Título I", "avô"),
            ],
        )
    assert gravados == 3


def test_substituir_apaga_o_que_sumiu_da_nova_redacao(corpus_cdc):
    conn = corpus_cdc
    restante = [
        disp(URN_CDC, ["tit1"], TipoDispositivo.TITULO, "Título I", "Dos Direitos", ordem=1)
    ]
    with db.transacao(conn):
        writer.substituir_dispositivos(conn, URN_CDC, restante)
    assert queries.obter_dispositivo(conn, INC8) is None
    assert conn.execute("SELECT COUNT(*) FROM dispositivo").fetchone()[0] == 1


def test_upsert_remissoes_e_idempotente(corpus_cdc):
    conn = corpus_cdc
    r = Remissao(origem_id=INC8, destino_urn=f"{URN_CF}!art5_inc35", texto_original="art. 5º, XXXV")
    with db.transacao(conn):
        writer.upsert_remissoes(conn, [r, r])
        writer.upsert_remissoes(conn, [r])
    assert conn.execute("SELECT COUNT(*) FROM remissao").fetchone()[0] == 1


def test_resolver_remissoes_liga_so_o_que_existe(corpus_cdc):
    conn = corpus_cdc
    with db.transacao(conn):
        writer.upsert_remissoes(
            conn,
            [
                Remissao(origem_id=INC8, destino_urn=ART6, texto_original="caput deste artigo"),
                Remissao(
                    origem_id=INC8,
                    destino_urn=f"{URN_CF}!art5_inc35",
                    texto_original="art. 5º, XXXV",  # norma ainda não ingerida
                ),
            ],
        )
        assert writer.resolver_remissoes_pendentes(conn) == 1

    remissoes = queries.remissoes_de(conn, INC8)
    resolvidas = {r.destino_urn: r.resolvida for r in remissoes}
    assert resolvidas[ART6] is True
    assert resolvidas[f"{URN_CF}!art5_inc35"] is False


def test_reingestao_desfaz_destino_pendurado(corpus_cdc):
    """Remissão de fora apontando para um ID que a reingestão apagou não pode ficar resolvida."""
    conn = corpus_cdc
    with db.transacao(conn):
        writer.upsert_remissoes(
            conn, [Remissao(origem_id=INC8, destino_urn=ART6, texto_original="caput")]
        )
        writer.resolver_remissoes_pendentes(conn)

    # o art. 6º sai da norma; a remissão que apontava para ele precisa voltar a pendente
    with db.transacao(conn):
        writer.substituir_dispositivos(
            conn,
            URN_CDC,
            [disp(URN_CDC, ["tit1"], TipoDispositivo.TITULO, "Título I", "x", ordem=1)],
        )
    penduradas = conn.execute(
        "SELECT COUNT(*) FROM remissao WHERE destino_id IS NOT NULL "
        "AND destino_id NOT IN (SELECT id FROM dispositivo)"
    ).fetchone()[0]
    assert penduradas == 0


def test_registrar_ingestao(corpus_cdc):
    conn = corpus_cdc
    with db.transacao(conn):
        writer.registrar_ingestao(conn, URN_CDC, "c" * 64, datetime(2026, 9, 21, 12, 0))
        writer.registrar_ingestao(conn, URN_CDC, "d" * 64, datetime(2026, 9, 22, 12, 0))
    assert queries.normas_indexadas(conn) == [(URN_CDC, "d" * 64)]


# --- queries --------------------------------------------------------------------------


def test_ancestrais_da_raiz_ate_o_pai(corpus_cdc):
    caminhos = [d.caminho for d in queries.ancestrais(corpus_cdc, INC8)]
    assert caminhos == ["tit1", "tit1/cap3", "tit1/cap3/art6"]


def test_obter_dispositivos_em_lote_deduplica(corpus_cdc):
    ausente = montar_urn_dispositivo(URN_CDC, "art999")
    achados = queries.obter_dispositivos(corpus_cdc, [INC8, ART6, INC8, ausente])
    assert set(achados) == {INC8, ART6}


def test_artigo_ancestral_sobe_do_inciso(corpus_cdc):
    assert queries.artigo_ancestral(corpus_cdc, INC8).id == ART6


def test_artigo_ancestral_do_proprio_artigo(corpus_cdc):
    assert queries.artigo_ancestral(corpus_cdc, ART6).id == ART6


def test_artigo_ancestral_de_no_estrutural_e_none(corpus_cdc):
    tit = montar_urn_dispositivo(URN_CDC, "tit1")
    assert queries.artigo_ancestral(corpus_cdc, tit) is None


def test_subarvore_em_ordem_de_documento(corpus_cdc, data_ref):
    """Ordenar por `caminho` colocaria inc10 antes de inc2 e embaralharia o artigo."""
    rotulos = [d.rotulo for d in queries.subarvore(corpus_cdc, ART6, data_referencia=data_ref)]
    assert rotulos == ["II", "VIII", "X"]


def test_subarvore_omite_revogado(corpus_cdc, data_ref):
    """O bug mais provável do sistema: o inciso revogado voltando pelo SQLite."""
    ids = {d.id for d in queries.subarvore(corpus_cdc, ART6, data_referencia=data_ref)}
    assert INC4_REVOGADO not in ids


def test_subarvore_traz_revogado_em_data_anterior(corpus_cdc):
    """Consulta sobre o direito vigente à época do fato: antes da revogação, ele valia."""
    ids = {
        d.id
        for d in queries.subarvore(corpus_cdc, ART6, data_referencia=date(2015, 1, 1))
    }
    assert INC4_REVOGADO in ids


def test_subarvore_omite_vacatio_legis(corpus_cdc, data_ref):
    """Publicado, mas sem vigência na data de referência."""
    ids = {d.id for d in queries.subarvore(corpus_cdc, ART6, data_referencia=data_ref)}
    assert INC11_FUTURO not in ids


def test_revogacao_vale_no_proprio_dia(corpus_cdc):
    """`revogado_em > ref`, não `>=` — tem de bater com o `range(gt=)` do Qdrant."""
    ids = {
        d.id
        for d in queries.subarvore(corpus_cdc, ART6, data_referencia=date(2020, 1, 1))
    }
    assert INC4_REVOGADO not in ids


def test_rotulo_completo_pula_niveis_estruturais(corpus_cdc):
    assert queries.rotulo_completo(corpus_cdc, INC8) == "Lei 8.078/1990, Art. 6º, VIII"


def test_iter_para_indexar_nao_filtra_vigencia(corpus_cdc):
    """O revogado precisa estar no índice para a consulta retroativa achá-lo."""
    ids = {d.id for d in queries.iter_para_indexar(corpus_cdc)}
    assert INC4_REVOGADO in ids and INC11_FUTURO in ids


def test_obter_dispositivo_inexistente_e_none(conn):
    assert queries.obter_dispositivo(conn, "urn:lex:br:federal:lei:1990-09-11;8078!art1") is None


def test_conexao_de_leitura_atravessa_threads(tmp_path, norma_cdc, arvore_cdc):
    """Defeito achado ao subir a API: a conexão nasce no lifespan e é usada pelas threads
    do threadpool — é assim que o FastAPI despacha endpoints `def`. Sem isso, a primeira
    consulta morre com "SQLite objects created in a thread can only be used in that same
    thread", e só em produção, porque todo teste roda numa thread só."""
    import threading

    caminho = tmp_path / "corpus.sqlite3"
    escrita = db.conectar(caminho)
    try:
        db.aplicar_schema(escrita)
        with db.transacao(escrita):
            writer.upsert_norma(escrita, norma_cdc)
            writer.substituir_dispositivos(escrita, URN_CDC, arvore_cdc)
    finally:
        escrita.close()

    leitura = db.conectar(caminho, somente_leitura=True)
    resultado: list = []

    def consultar():
        try:
            resultado.append(len(queries.iter_para_indexar(leitura).__next__().id))
        except Exception as e:  # noqa: BLE001 - é o erro que o teste existe para pegar
            resultado.append(e)

    t = threading.Thread(target=consultar)
    t.start()
    t.join()
    leitura.close()

    assert not isinstance(resultado[0], Exception), resultado[0]
