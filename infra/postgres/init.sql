-- Esquema de base de datos para el Sistema de Trading Forex en TimescaleDB

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Tabla de Velas OHLC (Hypertable optimizada para series de tiempo)
CREATE TABLE IF NOT EXISTS candles (
    pair          TEXT        NOT NULL,
    granularity   TEXT        NOT NULL,
    timestamp     TIMESTAMPTZ NOT NULL,
    open          DOUBLE PRECISION NOT NULL,
    high          DOUBLE PRECISION NOT NULL,
    low           DOUBLE PRECISION NOT NULL,
    close         DOUBLE PRECISION NOT NULL,
    volume        INTEGER DEFAULT 0,
    complete      BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (pair, granularity, timestamp)
);

SELECT create_hypertable('candles', 'timestamp', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_candles_pair_gran
    ON candles (pair, granularity, timestamp DESC);

-- 2. Tabla de Señales generadas por los motores de estrategia
CREATE TABLE IF NOT EXISTS signals (
    id            SERIAL PRIMARY KEY,
    pair          TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    direction     TEXT NOT NULL,
    confidence    DOUBLE PRECISION DEFAULT 1.0,
    timestamp     TIMESTAMPTZ NOT NULL,
    metadata      JSONB,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signals_pair_time
    ON signals (pair, timestamp DESC);

-- 3. Tabla de Órdenes y Posiciones
CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL PRIMARY KEY,
    pair            TEXT NOT NULL,
    direction       TEXT NOT NULL,
    units           INTEGER NOT NULL,
    entry_price     DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION,
    take_profit     DOUBLE PRECISION,
    status          TEXT DEFAULT 'pending',
    oanda_order_id  TEXT,
    opened_at       TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    pnl             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders (status, opened_at DESC);

-- 4. Vista de Resumen de Rendimiento por Estrategia
CREATE OR REPLACE VIEW v_strategy_performance AS
SELECT
    COALESCE(s.strategy_name, 'Desconocida') AS strategy,
    COUNT(o.id) AS total_trades,
    COUNT(CASE WHEN o.pnl > 0 THEN 1 END) AS winning_trades,
    COUNT(CASE WHEN o.pnl < 0 THEN 1 END) AS losing_trades,
    ROUND(CAST(AVG(CASE WHEN o.pnl > 0 THEN 1.0 ELSE 0.0 END) * 100 AS numeric), 2) AS win_rate_pct,
    ROUND(CAST(SUM(COALESCE(o.pnl, 0)) AS numeric), 2) AS total_pnl_usd,
    ROUND(CAST(AVG(COALESCE(o.pnl, 0)) AS numeric), 2) AS avg_trade_pnl
FROM orders o
LEFT JOIN signals s ON o.pair = s.pair AND ABS(EXTRACT(EPOCH FROM (o.opened_at - s.timestamp))) < 300
WHERE o.status = 'closed'
GROUP BY s.strategy_name;

-- 5. Vista de PnL Diario
CREATE OR REPLACE VIEW v_daily_pnl AS
SELECT
    DATE_TRUNC('day', closed_at) AS trade_date,
    COUNT(*) AS trades_count,
    ROUND(CAST(SUM(pnl) AS numeric), 2) AS daily_pnl_usd,
    ROUND(CAST(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) AS numeric), 2) AS gross_profit,
    ROUND(CAST(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) AS numeric), 2) AS gross_loss
FROM orders
WHERE status = 'closed' AND closed_at IS NOT NULL
GROUP BY DATE_TRUNC('day', closed_at)
ORDER BY trade_date DESC;