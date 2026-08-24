PRAGMA foreign_keys = ON;

BEGIN;

-- Legacy evidence is kept explicitly separate from the new semantic model.
-- These tables are archival: they preserve what the old system computed without
-- pretending that those outputs are ground truth for the new models.

CREATE TABLE IF NOT EXISTS legacy_correlations (
    legacy_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_noticia TEXT,
    ticker TEXT,
    es_contagio INTEGER,
    sentimiento REAL,
    fiabilidad_fuente REAL,
    divergencia_previa_pct REAL,
    precio_instante REAL,
    precio_mfe_60m REAL,
    impacto_mfe_60m_pct REAL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_database TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_legacy_corr_news ON legacy_correlations(id_noticia);
CREATE INDEX IF NOT EXISTS idx_legacy_corr_ticker ON legacy_correlations(ticker);

CREATE TABLE IF NOT EXISTS legacy_state_vectors (
    legacy_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_noticia TEXT,
    ticker TEXT,
    rsi REAL,
    momentum_pct REAL,
    atr REAL,
    vix REAL,
    tnx REAL,
    petroleo REAL,
    dolar REAL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_database TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_legacy_state_news ON legacy_state_vectors(id_noticia);
CREATE INDEX IF NOT EXISTS idx_legacy_state_ticker ON legacy_state_vectors(ticker);

CREATE TABLE IF NOT EXISTS legacy_paper_trading (
    legacy_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_operacion INTEGER,
    id_noticia TEXT,
    ticker TEXT,
    fecha_senal TEXT,
    horizonte_horas INTEGER,
    rendimiento_esperado_pct REAL,
    certeza_pct REAL,
    precio_entrada REAL,
    precio_salida_real REAL,
    rendimiento_real_pct REAL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_database TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_legacy_paper_ticker ON legacy_paper_trading(ticker);

COMMIT;
