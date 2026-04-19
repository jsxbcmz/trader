-- =============================================================================
-- kline_sync_state — bookkeeping for the background preload job.
--
-- One row per (market, symbol, period) tracking what we've already pulled from
-- the upstream source. The PreloadJobRunner consults this table on every pass:
--
--   SELECT last_synced_ts FROM kline_sync_state WHERE ...
--
-- and uses (last_synced_ts - 1 period) as the {@code fromDate} for the next
-- incremental fetch. First-ever pass gets the floor (now − 2500 × period).
--
-- {@code last_attempt_ts} and {@code last_error} are populated even on failures
-- so the admin/log layer can spot stuck symbols.
-- =============================================================================

CREATE TABLE IF NOT EXISTS kline_sync_state (
    market           VARCHAR(4)   NOT NULL,
    symbol           VARCHAR(20)  NOT NULL,
    period           VARCHAR(16)  NOT NULL,    -- '1min', '5min', ..., 'monthly'
    last_synced_ts   TIMESTAMPTZ,                -- the latest ts we've successfully stored
    last_attempt_ts  TIMESTAMPTZ,                -- when we last tried (success OR failure)
    last_error       VARCHAR(512),               -- truncated error message; null on success
    rows_total       BIGINT NOT NULL DEFAULT 0,  -- total rows ever inserted for this tuple (for stats)
    PRIMARY KEY (market, symbol, period)
);

CREATE INDEX IF NOT EXISTS idx_sync_state_attempt
    ON kline_sync_state (last_attempt_ts);

-- Ghost-stock detection columns (added later — idempotent migration)
ALTER TABLE kline_sync_state ADD COLUMN IF NOT EXISTS consecutive_failures INT NOT NULL DEFAULT 0;
ALTER TABLE kline_sync_state ADD COLUMN IF NOT EXISTS ghost_until TIMESTAMPTZ;

-- preload_config — single-row table holding the user's preload preferences.
-- Used by PreloadJobRunner on startup to know which markets to scan, and updated
-- by the frontend "preload markets" dialog when the user picks/changes selection.
CREATE TABLE IF NOT EXISTS preload_config (
    id           INT PRIMARY KEY DEFAULT 1,
    markets_csv  VARCHAR(64),                  -- 'CN,HK,US' (any subset, comma-separated, in code order)
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT preload_config_singleton CHECK (id = 1)
);

