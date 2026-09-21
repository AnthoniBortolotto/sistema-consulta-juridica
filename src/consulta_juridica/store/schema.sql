-- Fonte da verdade do corpus. O índice Qdrant é derivado deste banco e reconstruível
-- a partir dele; o inverso não é verdade.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS norma (
    urn              TEXT PRIMARY KEY,
    tipo             TEXT NOT NULL,
    numero           TEXT,
    ano              INTEGER NOT NULL,
    data_publicacao  TEXT,
    apelido          TEXT,
    ementa           TEXT,
    fonte_url        TEXT NOT NULL,
    sha256_origem    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositivo (
    id               TEXT PRIMARY KEY,
    norma_urn        TEXT NOT NULL REFERENCES norma(urn) ON DELETE CASCADE,
    parent_id        TEXT REFERENCES dispositivo(id) ON DELETE CASCADE,
    tipo             TEXT NOT NULL,
    rotulo           TEXT NOT NULL,
    ordem            INTEGER NOT NULL,
    -- materialized path: evita CTE recursiva para ancestrais e subárvore
    caminho          TEXT NOT NULL,
    texto            TEXT NOT NULL,
    vigencia_inicio  TEXT,
    revogado_em      TEXT,
    nota_alteracao   TEXT
);

CREATE INDEX IF NOT EXISTS ix_dispositivo_norma   ON dispositivo(norma_urn);
CREATE INDEX IF NOT EXISTS ix_dispositivo_parent  ON dispositivo(parent_id);
CREATE INDEX IF NOT EXISTS ix_dispositivo_caminho ON dispositivo(caminho);

CREATE TABLE IF NOT EXISTS remissao (
    id              INTEGER PRIMARY KEY,
    origem_id       TEXT NOT NULL REFERENCES dispositivo(id) ON DELETE CASCADE,
    destino_urn     TEXT NOT NULL,
    destino_id      TEXT,
    texto_original  TEXT NOT NULL,
    resolvida       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_remissao_origem  ON remissao(origem_id);
CREATE INDEX IF NOT EXISTS ix_remissao_destino ON remissao(destino_id);

-- Chave natural da remissão. A PK é sintética, então sem este índice o `upsert_remissoes`
-- não teria em que conflitar e reingerir a mesma norma duplicaria cada referência
-- cruzada. O mesmo dispositivo pode citar o mesmo destino duas vezes com texto diferente
-- ("art. 37, § 6º" e "parágrafo anterior"), por isso o texto entra na chave.
CREATE UNIQUE INDEX IF NOT EXISTS ux_remissao
    ON remissao(origem_id, destino_urn, texto_original);

-- Procedência: permite pular o reprocessamento quando a fonte não mudou.
CREATE TABLE IF NOT EXISTS ingestao (
    norma_urn     TEXT PRIMARY KEY REFERENCES norma(urn) ON DELETE CASCADE,
    sha256_origem TEXT NOT NULL,
    quando        TEXT NOT NULL
);
