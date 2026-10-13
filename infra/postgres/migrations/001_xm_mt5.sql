-- Migración para bases de datos creadas con la versión anterior de init.sql.
-- Es idempotente: se puede ejecutar varias veces sin romper nada.
--
-- Uso (con el contenedor de desarrollo corriendo):
--   docker exec -i forex_timescaledb_dev psql -U forex -d forex_data < infra/postgres/migrations/001_xm_mt5.sql

-- oanda_order_id -> broker_ticket
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'orders' AND column_name = 'oanda_order_id') THEN
        ALTER TABLE orders RENAME COLUMN oanda_order_id TO broker_ticket;
    END IF;
END $$;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS volume_lots     DOUBLE PRECISION;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS execution_mode  TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS broker_ticket   TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS strategy_name   TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signal_id       INTEGER;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS risk_amount     DOUBLE PRECISION;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS exit_price      DOUBLE PRECISION;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS exit_reason     TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS error           TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS metadata        JSONB;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMPTZ DEFAULT now();

-- Las órdenes simuladas anteriores quedan marcadas como tales.
UPDATE orders SET execution_mode = 'sim'
WHERE execution_mode IS NULL AND broker_ticket LIKE 'SIM_%';

-- Órdenes de la versión anterior que siguen "abiertas" no tienen datos para
-- reconciliarse contra el broker: se cancelan para que no bloqueen los límites de riesgo.
UPDATE orders SET status = 'cancelled', error = 'Cancelada en la migración a XM/MT5'
WHERE status IN ('pending', 'open', 'filled') AND execution_mode IS NULL;

DROP VIEW IF EXISTS v_strategy_performance;
CREATE VIEW v_strategy_performance AS
SELECT
    COALESCE(strategy_name, 'Desconocida') AS strategy,
    COUNT(id) AS total_trades,
    COUNT(CASE WHEN pnl > 0 THEN 1 END) AS winning_trades,
    COUNT(CASE WHEN pnl < 0 THEN 1 END) AS losing_trades,
    ROUND(CAST(AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100 AS numeric), 2) AS win_rate_pct,
    ROUND(CAST(SUM(COALESCE(pnl, 0)) AS numeric), 2) AS total_pnl_usd,
    ROUND(CAST(AVG(COALESCE(pnl, 0)) AS numeric), 2) AS avg_trade_pnl
FROM orders
WHERE status = 'closed'
GROUP BY strategy_name;

-- Las velas guardadas por la versión anterior desde MT5 tenían la hora mal
-- (hora del servidor convertida como si fuera hora local de tu PC) e incluían
-- la vela en formación. Mezcladas con las nuevas (en UTC real) producirían
-- velas duplicadas desfasadas. Son datos derivados: el data-collector vuelve a
-- descargar el historial (COLLECTOR_BACKFILL_CANDLES) al arrancar.
DELETE FROM candles;
