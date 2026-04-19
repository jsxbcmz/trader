-- Strategy table for Groovy script-based strategies
CREATE TABLE IF NOT EXISTS strategy (
    id          VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    script      TEXT NOT NULL,
    built_in    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

