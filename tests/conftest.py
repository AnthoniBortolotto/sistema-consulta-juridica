"""Fixtures compartilhadas."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from consulta_juridica.models import Dispositivo, Norma, TipoDispositivo
from consulta_juridica.store import db, writer
from consulta_juridica.urn import montar_caminho, montar_urn_dispositivo

DIR_FIXTURES = Path(__file__).parent / "fixtures"

URN_CDC = "urn:lex:br:federal:lei:1990-09-11;8078"
URN_CF = "urn:lex:br:federal:constituicao:1988-10-05;1988"


@pytest.fixture
def data_ref() -> date:
    """Data de referência fixa. Nunca `date.today()` em teste — o resultado mudaria sozinho."""
    return date(2026, 1, 1)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """SQLite em memória com o schema aplicado."""
    c = db.conectar(Path(db.EM_MEMORIA))
    db.aplicar_schema(c)
    yield c
    c.close()


def disp(
    norma_urn: str,
    segmentos: list[str],
    tipo: TipoDispositivo,
    rotulo: str,
    texto: str,
    *,
    ordem: int = 1,
    parent: list[str] | None = None,
    vigencia_inicio: date | None = None,
    revogado_em: date | None = None,
) -> Dispositivo:
    """Monta um Dispositivo pelos segmentos do caminho, derivando id e parent_id."""
    caminho = montar_caminho(segmentos)
    return Dispositivo(
        id=montar_urn_dispositivo(norma_urn, caminho),
        norma_urn=norma_urn,
        parent_id=(
            montar_urn_dispositivo(norma_urn, montar_caminho(parent)) if parent else None
        ),
        tipo=tipo,
        rotulo=rotulo,
        ordem=ordem,
        caminho=caminho,
        texto=texto,
        vigencia_inicio=vigencia_inicio,
        revogado_em=revogado_em,
    )


@pytest.fixture
def corpus_cdc(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Art. 6º do CDC, reduzido: caput mais três incisos, um deles revogado em 2020.

    É o formato que a expansão precisa exercitar — o inciso VIII isolado é ininteligível
    sem o caput, e o inciso revogado é o que não pode voltar pela porta do SQLite.
    """
    T = TipoDispositivo
    norma = Norma(
        urn=URN_CDC,
        tipo="lei",
        numero="8078",
        ano=1990,
        data_publicacao=date(1990, 9, 11),
        apelido="CDC",
        fonte_url="https://www.planalto.gov.br/ccivil_03/leis/l8078.htm",
        sha256_origem="0" * 64,
    )
    disps = [
        disp(URN_CDC, ["tit1"], T.TITULO, "Título I", "Dos Direitos do Consumidor", ordem=1),
        disp(URN_CDC, ["tit1", "cap3"], T.CAPITULO, "Capítulo III", "Dos Direitos Básicos",
             ordem=1, parent=["tit1"]),
        disp(URN_CDC, ["tit1", "cap3", "art6"], T.ARTIGO, "Art. 6º",
             "São direitos básicos do consumidor:", ordem=1, parent=["tit1", "cap3"]),
        # ordem fora de sequência de propósito: o inciso X não pode vir antes do II
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc10"], T.INCISO, "X",
             "a adequada e eficaz prestação dos serviços públicos em geral.",
             ordem=10, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc2"], T.INCISO, "II",
             "a educação e divulgação sobre o consumo adequado;",
             ordem=2, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc8"], T.INCISO, "VIII",
             "a facilitação da defesa de seus direitos, inclusive com a inversão do ônus "
             "da prova, a seu favor, no processo civil;",
             ordem=8, parent=["tit1", "cap3", "art6"]),
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc4"], T.INCISO, "IV",
             "inciso hipotético revogado, para o teste de vigência;",
             ordem=4, parent=["tit1", "cap3", "art6"], revogado_em=date(2020, 1, 1)),
        # vacatio legis: publicado, sem vigência na data de referência dos testes
        disp(URN_CDC, ["tit1", "cap3", "art6", "inc11"], T.INCISO, "XI",
             "inciso hipotético que só entra em vigor em 2030;",
             ordem=11, parent=["tit1", "cap3", "art6"], vigencia_inicio=date(2030, 1, 1)),
    ]
    with db.transacao(conn):
        writer.upsert_norma(conn, norma)
        writer.substituir_dispositivos(conn, URN_CDC, disps)
    return conn
