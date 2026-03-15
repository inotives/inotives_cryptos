-- migrate:up

-- -----------------------------------------------------------------------------
-- BACKTEST RUNS
-- Stores configuration and results for each strategy backtest run.
-- Used to compare parameter sets and track which config was promoted to live.
--
-- The backtesting engine (Section 11) simulates grid fills against historical
-- OHLCV candles (from asset_metrics_intraday or asset_metrics_1d) and writes
-- result metrics here when complete.
--
-- parameters JSONB captures the full strategy config snapshot at run time so
-- results are always reproducible even if the live strategy config changes.
--
-- Example parameters (DCA Grid):
--   {
--     "num_levels": 5,
--     "atr_multiplier_low": 0.4,
--     "atr_multiplier_normal": 0.5,
--     "atr_multiplier_high": 0.7,
--     "profit_target_low": 1.0,
--     "profit_target_normal": 1.5,
--     "profit_target_high": 2.5,
--     "capital_per_cycle": 1000,
--     "weights": [1, 1, 2, 3, 3],
--     "max_inventory_pct": 40,
--     "reserve_capital_pct": 30,
--     "circuit_breaker_atr_pct": 8
--   }
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.backtest_runs (
    id          BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT REFERENCES inotives_tradings.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,  -- nullable: can run without a live strategy
    asset_id    BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)  DEFERRABLE INITIALLY DEFERRED,

    name        TEXT NOT NULL,   -- e.g. 'BTC DCA Grid — normal multiplier 0.5'
    description TEXT,

    -- Backtest scope
    timeframe  TEXT NOT NULL,    -- '5m' | '1h' | '1d' — source candle resolution
    start_date DATE NOT NULL,
    end_date   DATE NOT NULL,

    -- Strategy parameters used for this run (full snapshot for reproducibility)
    parameters JSONB NOT NULL DEFAULT '{}',

    -- Lifecycle
    status        TEXT NOT NULL DEFAULT 'PENDING',  -- 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    error_message TEXT,  -- populated if status = 'FAILED'

    -- Result metrics (all nullable — populated when status = 'COMPLETED')
    total_return_pct        NUMERIC(10, 6),  -- Net return over the test period
    max_drawdown_pct        NUMERIC(10, 6),  -- Largest peak-to-trough drop
    win_rate                NUMERIC(10, 6),  -- % of cycles that closed at profit
    sharpe_ratio            NUMERIC(10, 6),  -- Risk-adjusted return
    profit_factor           NUMERIC(10, 6),  -- Gross profit / gross loss
    total_cycles            INTEGER,         -- Total grid cycles completed
    avg_cycle_duration_secs BIGINT,          -- Average time from cycle open to close

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT chk_backtest_timeframe CHECK (timeframe IN ('5m', '1h', '1d')),
    CONSTRAINT chk_backtest_status    CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    CONSTRAINT chk_backtest_date_range CHECK (end_date > start_date)
);

-- Most common queries: results for a given asset or strategy, sorted by recency
CREATE INDEX ON inotives_tradings.backtest_runs (asset_id, created_at DESC);
CREATE INDEX ON inotives_tradings.backtest_runs (strategy_id, created_at DESC) WHERE strategy_id IS NOT NULL;
CREATE INDEX ON inotives_tradings.backtest_runs (status);

CREATE TRIGGER auditing_trigger_backtest_runs
    BEFORE INSERT OR UPDATE ON inotives_tradings.backtest_runs
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.backtest_runs CASCADE;
