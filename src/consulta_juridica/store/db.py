"""Conexão e schema do SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

CAMINHO_SCHEMA = Path(__file__).parent / "schema.sql"

#: Banco em memória. Aceito por `conectar` para os testes não tocarem o disco.
EM_MEMORIA = ":memory:"


#: O módulo `sqlite3` foi compilado em modo serializado? Nesse modo (3) o próprio SQLite
#: protege a conexão com mutex, e compartilhá-la entre threads é seguro; em modo 1 não é.
#: A conexão de leitura nasce no lifespan da API e é usada pelas threads do threadpool —
#: é assim que o FastAPI despacha endpoints `def`, e sem isto a primeira consulta morre
#: com "SQLite objects created in a thread can only be used in that same thread".
#:
#: Derivar a flag da garantia real, em vez de fixar `False`, faz uma instalação sem modo
#: serializado falhar alto na primeira requisição em vez de correr com data race silencioso.
COMPARTILHAVEL_ENTRE_THREADS = sqlite3.threadsafety >= 3


def conectar(caminho: Path, *, somente_leitura: bool = False) -> sqlite3.Connection:
    """Abre a conexão.

    `somente_leitura=True` no caminho de consulta: recuperação e geração nunca escrevem,
    e deixar o banco impedir isso é mais barato que confiar na disciplina. É também a
    conexão que atravessa threads (ver `COMPARTILHAVEL_ENTRE_THREADS`): sem escrita não há
    transação entrelaçada a proteger, e o mutex do SQLite cuida do resto.

    `isolation_level=None` desliga o gerenciamento implícito de transação do módulo
    `sqlite3`: quem delimita é `transacao`, com BEGIN/COMMIT explícitos. Sem isso, o
    BEGIN de `transacao` cairia dentro de uma transação já aberta e levantaria erro.
    """
    if str(caminho) == EM_MEMORIA:
        conn = sqlite3.connect(EM_MEMORIA, isolation_level=None)
    elif somente_leitura:
        # `mode=ro` falha se o arquivo não existe, em vez de criar um banco vazio —
        # consulta contra corpus inexistente tem de ser erro, não zero resultados.
        conn = sqlite3.connect(
            f"file:{caminho.as_posix()}?mode=ro",
            uri=True,
            isolation_level=None,
            check_same_thread=not COMPARTILHAVEL_ENTRE_THREADS,
        )
    else:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(caminho, isolation_level=None)

    conn.row_factory = sqlite3.Row
    # Por conexão, não por banco: o PRAGMA do schema.sql não basta.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not somente_leitura and str(caminho) != EM_MEMORIA:
        # WAL deixa a API ler enquanto a ingestão escreve. É propriedade do arquivo:
        # basta ligar uma vez, e ligar de novo é no-op.
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def aplicar_schema(conn: sqlite3.Connection) -> None:
    """Executa `schema.sql` (idempotente)."""
    conn.executescript(CAMINHO_SCHEMA.read_text(encoding="utf-8"))
    # `executescript` commita e reabre a transação implícita; o PRAGMA dentro do arquivo
    # é ignorado nesse contexto. Reafirmado aqui para a conexão não ficar sem FK.
    conn.execute("PRAGMA foreign_keys = ON")


@contextmanager
def transacao(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit no sucesso, rollback na exceção.

    Não aninha: um BEGIN dentro de outro levanta `OperationalError`. É intencional —
    transação aninhada que "funciona" só finge atomicidade.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
