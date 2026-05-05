CREATE TABLE IF NOT EXISTS ma_daily (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    ma5 DOUBLE PRECISION,
    ma10 DOUBLE PRECISION,
    ma20 DOUBLE PRECISION,
    ma30 DOUBLE PRECISION,
    ma60 DOUBLE PRECISION
);
SELECT create_hypertable('ma_daily', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ma_daily ON ma_daily (symbol, market, time DESC);

CREATE TABLE IF NOT EXISTS macd_daily (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    dif DOUBLE PRECISION,
    dea DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION
);
SELECT create_hypertable('macd_daily', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_macd_daily ON macd_daily (symbol, market, time DESC);

CREATE TABLE IF NOT EXISTS rsi_daily (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    rsi6 DOUBLE PRECISION,
    rsi12 DOUBLE PRECISION,
    rsi24 DOUBLE PRECISION
);
SELECT create_hypertable('rsi_daily', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rsi_daily ON rsi_daily (symbol, market, time DESC);

CREATE TABLE IF NOT EXISTS boll_daily (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    upper_band DOUBLE PRECISION,
    middle_band DOUBLE PRECISION,
    lower_band DOUBLE PRECISION
);
SELECT create_hypertable('boll_daily', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_boll_daily ON boll_daily (symbol, market, time DESC);
