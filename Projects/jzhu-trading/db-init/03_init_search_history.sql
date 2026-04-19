CREATE TABLE IF NOT EXISTS search_history (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    period VARCHAR(10) NOT NULL DEFAULT 'daily',
    start_date VARCHAR(10) NOT NULL,
    end_date VARCHAR(10) NOT NULL,
    chart_settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(symbol, market)
);

-- Add column if table already exists (idempotent migration)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'search_history' AND column_name = 'chart_settings') THEN
        ALTER TABLE search_history ADD COLUMN chart_settings JSONB DEFAULT '{}';
    END IF;
END $$;
