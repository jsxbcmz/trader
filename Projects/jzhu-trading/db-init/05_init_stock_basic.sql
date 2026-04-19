-- =============================================================================
-- Stock list cache — one row per (market, symbol).
--
-- Populated lazily by GetStockListUseCase: first request for a given market
-- pulls the full list from AKTools/baostock and writes here, subsequent requests
-- read from this table. The `updated_at` column lets us refresh stale entries.
-- =============================================================================

CREATE TABLE IF NOT EXISTS stock_basic (
    market      VARCHAR(4)  NOT NULL,                  -- 'CN' / 'HK' / 'US'
    symbol      VARCHAR(20) NOT NULL,                  -- bare code, no exchange prefix
    name        VARCHAR(128),                          -- display name (any language)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market, symbol)
);

CREATE INDEX IF NOT EXISTS idx_stock_basic_market ON stock_basic (market);

-- ── Enrichment columns (added 2026-04) ───────────────────────────────────────
-- These hold the rich metadata baostock-svc produces in /api/cn/stock-classification
-- and let stock_basic serve as the authoritative symbol source for preload.
-- All ALTERs are idempotent so re-running the init script is safe.
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS exchange            VARCHAR(8);
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS board               VARCHAR(16);
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS status              VARCHAR(8);
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS industry            VARCHAR(64);
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS list_date           DATE;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS last_close          DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS pe_ttm              DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS pb                  DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS ps_ttm              DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS turnover_rate       DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS volume              DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS amount              DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS total_mv            DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS circ_mv             DOUBLE PRECISION;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS delisted            BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS enabled_for_preload BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS last_refreshed_at   TIMESTAMPTZ;

-- Partial index used by PreloadJobRunner.findEnabledForPreload — only the rows
-- the preload worker actually iterates over.
CREATE INDEX IF NOT EXISTS idx_stock_basic_preload
    ON stock_basic (market, symbol)
    WHERE enabled_for_preload = TRUE AND delisted = FALSE;
