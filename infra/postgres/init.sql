-- =====================================================================================
--  Esquema de base de datos del Sistema de Trading Forex · versión 2 (2026-10-26)
--
--  Requisitos: PostgreSQL 13+. TimescaleDB es OPCIONAL: si la extensión está disponible
--  `candles` se convierte en hypertable; si no, queda como tabla normal y todo funciona igual.
--
--  Es IDEMPOTENTE: se puede ejecutar sobre una BD vacía o sobre una ya creada, las veces
--  que haga falta, sin perder datos.
--    · Docker (docker-compose*.yml): se ejecuta solo al crear el volumen por primera vez.
--    · BD ya existente (Dokploy, servidor, otra PC):  python scripts/init_db.py
--
--  Reglas de diseño (no romper al modificar):
--    · Precios y montos en DOUBLE PRECISION: el código opera con float; NUMERIC devolvería
--      Decimal y rompería la aritmética del simulador y de la reconciliación.
--    · Horas siempre TIMESTAMPTZ (UTC).
--    · Las restricciones solo pueden fallar al RESERVAR una orden (el risk-manager la rechaza:
--      fail-closed). Nunca al marcarla filled/closed: eso dejaría una posición real abierta
--      sin registrar. Por eso exit_reason, execution_mode y units no llevan CHECK.
-- =====================================================================================

SET client_min_messages = warning;

-- -------------------------------------------------------------------------------------
-- 0. Extensión TimescaleDB (si existe en el servidor)
-- -------------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        BEGIN
            CREATE EXTENSION IF NOT EXISTS timescaledb;
        EXCEPTION WHEN others THEN
            RAISE WARNING 'TimescaleDB está instalada pero no se pudo activar (%). Se usan tablas normales.', SQLERRM;
        END;
    END IF;
END $$;

-- Helper de sesión: agrega una restricción solo si no existe. Si los datos existentes la
-- violan, la crea NOT VALID (se aplica a filas nuevas) y avisa.
CREATE OR REPLACE FUNCTION pg_temp.agregar_restriccion(tabla regclass, nombre text, definicion text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = tabla AND conname = nombre) THEN
        RETURN;
    END IF;
    BEGIN
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', tabla, nombre, definicion);
    EXCEPTION WHEN check_violation OR foreign_key_violation THEN
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s NOT VALID', tabla, nombre, definicion);
        RAISE WARNING 'Restricción % creada NOT VALID: hay filas antiguas que no la cumplen.', nombre;
    END;
END $$;

-- La BD trabaja en UTC (CLAUDE.md: horas siempre en UTC). Aplica a conexiones nuevas.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), 'UTC');
EXCEPTION WHEN insufficient_privilege THEN
    RAISE WARNING 'Sin permiso para fijar timezone=UTC en la BD; las vistas usan UTC igualmente.';
END $$;

-- Mantiene updated_at al día en cada UPDATE
CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- -------------------------------------------------------------------------------------
-- 1. Control de versiones del esquema
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER     PRIMARY KEY,
    description TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================================
--  TABLAS QUE USA EL SISTEMA HOY
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 2. candles — velas OHLC cerradas (escribe: data-collector · lee: strategy-engine,
--    simulador, backtester, Grafana)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candles (
    pair        TEXT             NOT NULL,              -- EUR_USD, XAU_USD...
    granularity TEXT             NOT NULL,              -- M1 M5 M15 M30 H1 H4 D D1 W1
    timestamp   TIMESTAMPTZ      NOT NULL,              -- apertura de la vela, UTC
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      BIGINT           NOT NULL DEFAULT 0,    -- volumen de ticks
    complete    BOOLEAN          NOT NULL DEFAULT TRUE,
    PRIMARY KEY (pair, granularity, timestamp)
);
SELECT pg_temp.agregar_restriccion('candles', 'ck_candles_granularity',
    $c$CHECK (granularity IN ('M1','M5','M15','M30','H1','H4','D','D1','W1'))$c$);
SELECT pg_temp.agregar_restriccion('candles', 'ck_candles_precios', 'CHECK (low > 0 AND low <= high)');

-- La PK (pair, granularity, timestamp) ya cubre "últimas N velas de un par": el índice
-- de la v1 era un duplicado.
DROP INDEX IF EXISTS idx_candles_pair_gran;

-- Hypertable con chunks de 30 días. Sin compresión a propósito: el volumen es pequeño y el
-- data-collector hace upsert sobre velas recientes (en chunks comprimidos podría fallar).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('candles', 'timestamp',
                                  chunk_time_interval => INTERVAL '30 days',
                                  if_not_exists => TRUE, migrate_data => TRUE);
    END IF;
END $$;

-- -------------------------------------------------------------------------------------
-- 3. signals — señales generadas por las estrategias (escribe: strategy-engine)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    pair          TEXT             NOT NULL,
    strategy_name TEXT             NOT NULL,
    direction     TEXT             NOT NULL,             -- buy | sell | close | flat
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    timestamp     TIMESTAMPTZ      NOT NULL,             -- vela que generó la señal, UTC
    metadata      JSONB            NOT NULL DEFAULT '{}', -- entry_price, stop_loss, take_profit, indicadores
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT now()
);
SELECT pg_temp.agregar_restriccion('signals', 'ck_signals_direction',
    $c$CHECK (direction IN ('buy','sell','close','flat'))$c$);
SELECT pg_temp.agregar_restriccion('signals', 'ck_signals_confidence', 'CHECK (confidence BETWEEN 0 AND 1)');

CREATE INDEX IF NOT EXISTS idx_signals_pair_time     ON signals (pair, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_strategy_time ON signals (strategy_name, timestamp DESC);

-- -------------------------------------------------------------------------------------
-- 4. orders — órdenes y posiciones (escribe: risk-manager reserva, execution-engine
--    ejecuta y reconcilia)
--    Ciclo: pending -> submitting -> filled -> closed      (o rejected / cancelled)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id             BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    pair           TEXT             NOT NULL,
    direction      TEXT             NOT NULL,            -- buy | sell
    units          BIGINT           NOT NULL,            -- unidades del activo (1 lote forex = 100.000)
    volume_lots    DOUBLE PRECISION,
    entry_price    DOUBLE PRECISION,                     -- solicitado al reservar; real tras el fill
    stop_loss      DOUBLE PRECISION,
    take_profit    DOUBLE PRECISION,
    status         TEXT             NOT NULL DEFAULT 'pending',
    execution_mode TEXT,                                 -- mt5 | sim (se fija al reclamarla)
    broker_ticket  TEXT,                                 -- ticket de la posición en MT5 (o SIM_xxx)
    strategy_name  TEXT,
    signal_id      BIGINT,
    risk_amount    DOUBLE PRECISION,                     -- pérdida si toca el SL, en divisa de la cuenta
    opened_at      TIMESTAMPTZ,
    closed_at      TIMESTAMPTZ,
    exit_price     DOUBLE PRECISION,
    exit_reason    TEXT,                                 -- tp | sl | manual | stop_out | expert ...
    pnl            DOUBLE PRECISION,                     -- divisa de la cuenta, con comisión y swap
    error          TEXT,
    metadata       JSONB            NOT NULL DEFAULT '{}', -- evaluación de riesgo y señal original
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now()
);
-- Resultado en múltiplos del riesgo (R): la misma unidad que usa la validación del backtest.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS r_multiple DOUBLE PRECISION
    GENERATED ALWAYS AS (CASE WHEN risk_amount > 0 THEN pnl / risk_amount END) STORED;

SELECT pg_temp.agregar_restriccion('orders', 'ck_orders_status',
    $c$CHECK (status IN ('pending','submitting','approved','rejected','filled','cancelled','closed'))$c$);
SELECT pg_temp.agregar_restriccion('orders', 'ck_orders_direction', $c$CHECK (direction IN ('buy','sell'))$c$);
SELECT pg_temp.agregar_restriccion('orders', 'fk_orders_signal',
    'FOREIGN KEY (signal_id) REFERENCES signals (id) ON DELETE SET NULL');

-- Segunda barrera de "un trade por par": si dos señales del mismo par llegaran a la vez,
-- la BD rechaza la segunda reserva y el risk-manager la descarta (fail-closed).
-- Solo puede fallar en el INSERT de 'pending', nunca al avanzar de estado una orden existente.
DO $$
BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_un_activo_por_par
        ON orders (pair) WHERE status IN ('pending', 'submitting', 'filled');
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING 'No se creó ux_orders_un_activo_por_par: hay pares con varias órdenes activas. Ciérralas o cancélalas y vuelve a ejecutar.';
END $$;

DROP INDEX IF EXISTS idx_orders_status;                              -- índice de la v1, reemplazado
CREATE INDEX IF NOT EXISTS idx_orders_status_updated ON orders (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_orders_closed_at      ON orders (closed_at DESC) WHERE status = 'closed';
CREATE INDEX IF NOT EXISTS idx_orders_strategy       ON orders (strategy_name, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_signal         ON orders (signal_id);

DROP TRIGGER IF EXISTS trg_orders_updated_at ON orders;
CREATE TRIGGER trg_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- =====================================================================================
--  TABLAS PREPARADAS PARA LAS PRÓXIMAS FASES (docs/PLAN_INTEGRACION_COLAB.md)
--  Ningún servicio escribe en ellas todavía.
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 5. account_snapshots — historial de balance y equity (curva de capital real en Grafana).
--    Lo escribirá el execution-engine junto al snapshot que hoy solo publica en Redis.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_snapshots (
    source           TEXT             NOT NULL,          -- simulated | mt5
    login            BIGINT,
    timestamp        TIMESTAMPTZ      NOT NULL,
    balance          DOUBLE PRECISION NOT NULL,
    equity           DOUBLE PRECISION NOT NULL,
    day_start_equity DOUBLE PRECISION,                   -- base del circuit breaker diario
    margin_used      DOUBLE PRECISION,
    margin_available DOUBLE PRECISION,
    open_positions   INTEGER,
    currency         TEXT             NOT NULL DEFAULT 'USD',
    is_real_account  BOOLEAN          NOT NULL DEFAULT FALSE,
    PRIMARY KEY (source, timestamp)
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('account_snapshots', 'timestamp',
                                  chunk_time_interval => INTERVAL '30 days',
                                  if_not_exists => TRUE, migrate_data => TRUE);
    END IF;
END $$;

-- -------------------------------------------------------------------------------------
-- 6. risk_events — rechazos y alertas de riesgo/sistema (hoy solo viven en el stream
--    risk:alerts de Redis). Permiten saber POR QUÉ no se operó. Lo escribirá monitoring.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_events (
    id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
    type          TEXT        NOT NULL,     -- signal_rejected | mode_changed | orphan_position | uncertain_order | ...
    pair          TEXT,
    strategy_name TEXT,
    reason        TEXT,
    signal_id     BIGINT REFERENCES signals (id) ON DELETE SET NULL,
    order_id      BIGINT REFERENCES orders (id)  ON DELETE SET NULL,
    metadata      JSONB       NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_risk_events_time      ON risk_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_type_time ON risk_events (type, timestamp DESC);

-- -------------------------------------------------------------------------------------
-- 7. backtest_runs — cada corrida de backtest/validación con su veredicto (fase 2 del plan).
--    Guarda lo necesario para reproducirla: datos, periodo, parámetros y commit del código.
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),
    strategy_name TEXT             NOT NULL,
    pairs         TEXT[]           NOT NULL,
    granularity   TEXT             NOT NULL,
    data_source   TEXT             NOT NULL,            -- mt5 | dukascopy | db | csv | synthetic
    period_start  DATE             NOT NULL,
    period_end    DATE             NOT NULL,
    oos_start     DATE,                                 -- inicio del periodo fuera de muestra
    params        JSONB            NOT NULL DEFAULT '{}', -- parámetros de la estrategia, costos, riesgo
    approvals     JSONB,                                -- aprobación por activo y modo (in-sample)
    metrics_is    JSONB,                                -- n, win, exp, pf, dd_pct, sharpe... in-sample
    metrics_oos   JSONB,                                -- lo mismo fuera de muestra
    p_value       DOUBLE PRECISION,                     -- test de permutación (OOS)
    ci_low        DOUBLE PRECISION,                     -- IC 95% de la expectativa en R (bootstrap)
    ci_high       DOUBLE PRECISION,
    verdict       TEXT,                                 -- VENTAJA CON EVIDENCIA | POSITIVO PERO NO CONCLUYENTE | INSUFICIENTE | SIN VENTAJA
    code_version  TEXT,                                 -- commit de git
    host          TEXT,                                 -- computadora donde se corrió
    notes         TEXT
);
SELECT pg_temp.agregar_restriccion('backtest_runs', 'ck_backtest_runs_periodo', 'CHECK (period_start <= period_end)');
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs (strategy_name, created_at DESC);

-- -------------------------------------------------------------------------------------
-- 8. backtest_trades — operaciones simuladas de cada corrida (en R, después de costos)
-- -------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_trades (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id      BIGINT           NOT NULL REFERENCES backtest_runs (id) ON DELETE CASCADE,
    pair        TEXT             NOT NULL,
    mode        TEXT,                                   -- confirmacion | limite | fibo | NULL
    sample      TEXT             NOT NULL,              -- is | oos
    direction   TEXT             NOT NULL,              -- buy | sell
    setup       TEXT,                                   -- Barrido PDL, Retroceso OB, ...
    entry_time  TIMESTAMPTZ      NOT NULL,
    exit_time   TIMESTAMPTZ,
    entry_price DOUBLE PRECISION NOT NULL,
    stop_loss   DOUBLE PRECISION NOT NULL,
    take_profit DOUBLE PRECISION,
    exit_price  DOUBLE PRECISION,
    exit_reason TEXT,                                   -- TP | SL | cierre | abierta
    r_gross     DOUBLE PRECISION,
    cost_r      DOUBLE PRECISION,
    r_net       DOUBLE PRECISION,
    r_opposite  DOUBLE PRECISION,                       -- misma operación al revés (test de permutación)
    included    BOOLEAN          NOT NULL DEFAULT TRUE, -- FALSE si el filtro de exposición la descartó
    metadata    JSONB            NOT NULL DEFAULT '{}'
);
SELECT pg_temp.agregar_restriccion('backtest_trades', 'ck_backtest_trades_sample', $c$CHECK (sample IN ('is','oos'))$c$);
SELECT pg_temp.agregar_restriccion('backtest_trades', 'ck_backtest_trades_direction', $c$CHECK (direction IN ('buy','sell'))$c$);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades (run_id, entry_time);

-- =====================================================================================
--  VISTAS (Grafana e informes). Se recrean siempre: no guardan datos.
--  El PnL se separa por execution_mode: mezclar simulación y cuenta real engaña.
-- =====================================================================================
DROP VIEW IF EXISTS v_strategy_performance, v_daily_pnl, v_pair_performance, v_open_positions,
                    v_equity_curve, v_signal_funnel, v_backtest_summary CASCADE;

-- 9. Rendimiento por estrategia y modo
CREATE VIEW v_strategy_performance AS
SELECT
    COALESCE(strategy_name, 'Desconocida')                                   AS strategy,
    execution_mode,
    COUNT(*)                                                                 AS total_trades,
    COUNT(*) FILTER (WHERE pnl > 0)                                          AS winning_trades,
    COUNT(*) FILTER (WHERE pnl < 0)                                          AS losing_trades,
    ROUND((AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100)::numeric, 2)  AS win_rate_pct,
    ROUND(SUM(COALESCE(pnl, 0))::numeric, 2)                                 AS total_pnl,
    ROUND(AVG(COALESCE(pnl, 0))::numeric, 2)                                 AS avg_trade_pnl,
    ROUND(AVG(r_multiple)::numeric, 3)                                       AS avg_r,
    ROUND((SUM(pnl) FILTER (WHERE pnl > 0)
           / NULLIF(ABS(SUM(pnl) FILTER (WHERE pnl < 0)), 0))::numeric, 2)   AS profit_factor
FROM orders
WHERE status = 'closed'
GROUP BY strategy_name, execution_mode;

-- 10. Rendimiento por par y modo
CREATE VIEW v_pair_performance AS
SELECT
    pair,
    execution_mode,
    COUNT(*)                                                                 AS total_trades,
    ROUND((AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100)::numeric, 2)  AS win_rate_pct,
    ROUND(SUM(COALESCE(pnl, 0))::numeric, 2)                                 AS total_pnl,
    ROUND(AVG(r_multiple)::numeric, 3)                                       AS avg_r
FROM orders
WHERE status = 'closed'
GROUP BY pair, execution_mode;

-- 11. PnL diario (día UTC)
CREATE VIEW v_daily_pnl AS
SELECT
    DATE_TRUNC('day', closed_at AT TIME ZONE 'UTC')                                    AS trade_date,
    execution_mode,
    COUNT(*)                                                        AS trades_count,
    ROUND(SUM(pnl)::numeric, 2)                                     AS daily_pnl,
    ROUND(COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0)::numeric, 2) AS gross_profit,
    ROUND(COALESCE(SUM(pnl) FILTER (WHERE pnl < 0), 0)::numeric, 2) AS gross_loss,
    ROUND(SUM(r_multiple)::numeric, 2)                              AS daily_r
FROM orders
WHERE status = 'closed' AND closed_at IS NOT NULL
GROUP BY DATE_TRUNC('day', closed_at AT TIME ZONE 'UTC'), execution_mode;

-- 12. Curva de resultados acumulados (PnL y R) por modo
CREATE VIEW v_equity_curve AS
SELECT
    closed_at,
    execution_mode,
    id AS order_id,
    pair,
    strategy_name,
    pnl,
    r_multiple,
    SUM(COALESCE(pnl, 0))        OVER w AS cumulative_pnl,
    SUM(COALESCE(r_multiple, 0)) OVER w AS cumulative_r
FROM orders
WHERE status = 'closed' AND closed_at IS NOT NULL
WINDOW w AS (PARTITION BY execution_mode ORDER BY closed_at, id);

-- 13. Posiciones abiertas ahora mismo
CREATE VIEW v_open_positions AS
SELECT
    id AS order_id, pair, direction, execution_mode, strategy_name, volume_lots,
    entry_price, stop_loss, take_profit, risk_amount, broker_ticket, opened_at,
    ROUND((EXTRACT(EPOCH FROM (now() - opened_at)) / 60)::numeric, 1) AS minutes_open
FROM orders
WHERE status = 'filled';

-- 14. Embudo diario (día UTC): señales -> órdenes reservadas -> ejecutadas -> cerradas
CREATE VIEW v_signal_funnel AS
SELECT
    DATE_TRUNC('day', s.timestamp AT TIME ZONE 'UTC')                                     AS day,
    s.strategy_name,
    COUNT(DISTINCT s.id)                                               AS signals,
    COUNT(DISTINCT o.id)                                               AS orders_reserved,
    COUNT(DISTINCT o.id) FILTER (WHERE o.status IN ('filled','closed')) AS orders_executed,
    COUNT(DISTINCT o.id) FILTER (WHERE o.status = 'closed')            AS orders_closed,
    COUNT(DISTINCT o.id) FILTER (WHERE o.status IN ('rejected','cancelled')) AS orders_failed
FROM signals s
LEFT JOIN orders o ON o.signal_id = s.id
GROUP BY DATE_TRUNC('day', s.timestamp AT TIME ZONE 'UTC'), s.strategy_name;

-- 15. Resumen de validaciones de backtest (lo más reciente primero)
CREATE VIEW v_backtest_summary AS
SELECT
    id, created_at, strategy_name, pairs, granularity, data_source, period_start, period_end, oos_start,
    (metrics_oos ->> 'n')::int          AS oos_trades,
    (metrics_oos ->> 'exp')::float8     AS oos_expectancy_r,
    (metrics_oos ->> 'pf')::float8      AS oos_profit_factor,
    (metrics_oos ->> 'dd_pct')::float8  AS oos_max_dd_pct,
    p_value, ci_low, ci_high, verdict, code_version, host
FROM backtest_runs;

-- -------------------------------------------------------------------------------------
-- Registro de versión
-- -------------------------------------------------------------------------------------
INSERT INTO schema_version (version, description)
VALUES (2, 'Esquema v2: TimescaleDB opcional, restricciones, R por orden, tablas de cuenta, riesgo y backtest, vistas')
ON CONFLICT (version) DO NOTHING;
