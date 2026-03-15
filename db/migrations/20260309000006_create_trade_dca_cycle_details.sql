-- migrate:up

-- -----------------------------------------------------------------------------
-- TRADE DCA CYCLE DETAILS
-- 1:1 extension of trade_cycles for DCA Grid strategy-specific live state.
--
-- Keeps DCA-specific fields out of the generic trade_cycles table, which is
-- designed to serve all strategy types (MOMENTUM, ARBITRAGE, etc.).
--
-- The bot writes this row when a cycle opens and updates it in-place on every
-- auto-tune event (regime shift). Column updates are used instead of JSONB
-- to keep per-tick writes simple and atomic.
--
-- Audit trail for each re-tune is handled by system_events:
--   bot_name   = 'trader_bot'
--   event_type = 'GRID_RETUNED'
--   payload    = { old: {...}, new: {...} }
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_dca_cycle_details (
    id          BIGSERIAL PRIMARY KEY,
    cycle_id    BIGINT NOT NULL UNIQUE REFERENCES inotives_tradings.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    strategy_id BIGINT NOT NULL        REFERENCES inotives_tradings.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,

    -- Immutable snapshot captured at cycle open
    atr_at_open NUMERIC(36, 18) NOT NULL,  -- ATR(14) value when the cycle was opened

    -- Live auto-tuning state (updated in-place on every regime change)
    atr_multiplier    NUMERIC(10, 6) NOT NULL,  -- Current regime multiplier: 0.4 | 0.5 | 0.7
    grid_spacing_pct  NUMERIC(10, 6) NOT NULL,  -- Current grid spacing as % of price
    profit_target_pct NUMERIC(10, 6) NOT NULL,  -- Current profit target: 1.0 | 1.5 | 2.0–3.0
    volatility_regime TEXT           NOT NULL,  -- Current regime: 'low' | 'normal' | 'high' | 'extreme'

    -- Timestamp of the most recent auto-tune update
    last_tuned_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT chk_dca_cycle_volatility_regime CHECK (
        volatility_regime IN ('low', 'normal', 'high', 'extreme')
    )
);

-- Bot query: fetch DCA tuning state for all open cycles of a strategy
CREATE INDEX ON inotives_tradings.trade_dca_cycle_details (strategy_id);

CREATE TRIGGER auditing_trigger_trade_dca_cycle_details
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_dca_cycle_details
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.trade_dca_cycle_details CASCADE;
