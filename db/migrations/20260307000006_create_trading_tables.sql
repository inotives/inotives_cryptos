-- migrate:up

-- ENUMs
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_strategy_status') THEN
        CREATE TYPE inotives_tradings.trade_strategy_status AS ENUM ('ACTIVE', 'PAUSED', 'ARCHIVED');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_cycle_status') THEN
        CREATE TYPE inotives_tradings.trade_cycle_status AS ENUM ('OPEN', 'CLOSING', 'CLOSED', 'CANCELLED');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_side') THEN
        CREATE TYPE inotives_tradings.trade_side AS ENUM ('BUY', 'SELL');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_order_type') THEN
        CREATE TYPE inotives_tradings.trade_order_type AS ENUM ('LIMIT', 'MARKET', 'STOP_LIMIT', 'STOP_MARKET');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_order_status') THEN
        CREATE TYPE inotives_tradings.trade_order_status AS ENUM ('PENDING', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED');
    END IF;
END $$;


-- -----------------------------------------------------------------------------
-- 1. TRADE STRATEGIES
-- Generic strategy registry. Identity + classification fields are fixed columns.
-- All strategy-type-specific parameters live in metadata.
--
-- Example metadata per strategy_type:
--   DCA_GRID:  { "capital_per_cycle": 1000, "num_slots": 10,
--                "entry_spacing_pct": 1.0, "take_profit_pct": 2.0,
--                "stop_loss_pct": 5.0 }
--   MOMENTUM:  { "lookback_period": 14, "entry_threshold": 0.03 }
--   ARBITRAGE: { "min_spread_pct": 0.5, "max_position_usd": 5000 }
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_strategies (
    id             BIGSERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    strategy_type  TEXT NOT NULL,  -- e.g. 'DCA_GRID', 'MOMENTUM', 'ARBITRAGE'

    -- The pair being traded and the specific account to execute on
    -- Exchange is derivable via venue_id → venues.source_id
    base_asset_id  BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)  DEFERRABLE INITIALLY DEFERRED,
    quote_asset_id BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)  DEFERRABLE INITIALLY DEFERRED,
    venue_id       BIGINT NOT NULL REFERENCES inotives_tradings.venues(id)  DEFERRABLE INITIALLY DEFERRED,

    -- Exchange fee rates for this strategy (affects net profit threshold calculation)
    -- Limit orders (DCA buys) use maker_fee_pct; exit sells may use taker_fee_pct.
    -- Net profit = gross_pnl - (total_buy_cost × maker_fee_pct) - (sell_proceeds × taker_fee_pct)
    maker_fee_pct NUMERIC(8, 6) NOT NULL DEFAULT 0,  -- e.g. 0.001000 = 0.1%
    taker_fee_pct NUMERIC(8, 6) NOT NULL DEFAULT 0,  -- e.g. 0.001000 = 0.1%

    status   inotives_tradings.trade_strategy_status NOT NULL DEFAULT 'ACTIVE',
    metadata JSONB NOT NULL DEFAULT '{}',  -- Strategy-type-specific parameters

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_trade_strategies CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE INDEX ON inotives_tradings.trade_strategies (strategy_type, status);
CREATE INDEX ON inotives_tradings.trade_strategies (venue_id);
CREATE INDEX ON inotives_tradings.trade_strategies (base_asset_id, quote_asset_id);

CREATE TABLE inotives_tradings.trade_strategies_history (LIKE inotives_tradings.trade_strategies INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.trade_strategies_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.trade_strategies_history (sys_period);
CREATE INDEX ON inotives_tradings.trade_strategies_history (changed_at);
CREATE INDEX ON inotives_tradings.trade_strategies_history (changed_by);

CREATE TRIGGER auditing_trigger_trade_strategies
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_strategies
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_trade_strategies
    BEFORE DELETE ON inotives_tradings.trade_strategies
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_trade_strategies
    BEFORE UPDATE OR DELETE ON inotives_tradings.trade_strategies
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.trade_strategies_history');


-- -----------------------------------------------------------------------------
-- 2. TRADE CYCLES
-- One activation/run of a strategy. Only universal lifecycle fields are columns.
-- All strategy-type-specific state (slot tracking, avg prices, etc.) lives in
-- metadata and gets updated by the application as the cycle progresses.
--
-- Example metadata per strategy_type:
--   DCA_GRID:  { "reference_price": 50000, "slots_total": 10, "slots_filled": 5,
--                "avg_buy_price": 49200, "quantity_held": 0.102,
--                "total_cost": 500.00 }
--   MOMENTUM:  { "entry_signal": "RSI_OVERSOLD", "entry_price": 48000 }
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_cycles (
    id           BIGSERIAL PRIMARY KEY,
    strategy_id  BIGINT  NOT NULL REFERENCES inotives_tradings.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,
    cycle_number INTEGER NOT NULL,  -- Incrementing counter per strategy (1, 2, 3 ...)

    capital_allocated NUMERIC(36, 8) NOT NULL,  -- Quote currency deployed for this cycle

    -- Lifecycle
    status        inotives_tradings.trade_cycle_status NOT NULL DEFAULT 'OPEN',
    close_trigger TEXT,                   -- 'take_profit' | 'stop_loss' | 'manual'
    opened_at     TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    closed_at     TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}',  -- Strategy-type-specific cycle state

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT uq_trade_cycles UNIQUE (strategy_id, cycle_number),
    CONSTRAINT chk_deleted_fields_trade_cycles CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE INDEX ON inotives_tradings.trade_cycles (strategy_id, status);

CREATE TABLE inotives_tradings.trade_cycles_history (LIKE inotives_tradings.trade_cycles INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.trade_cycles_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.trade_cycles_history (sys_period);
CREATE INDEX ON inotives_tradings.trade_cycles_history (changed_at);
CREATE INDEX ON inotives_tradings.trade_cycles_history (changed_by);

CREATE TRIGGER auditing_trigger_trade_cycles
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_cycles
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_trade_cycles
    BEFORE DELETE ON inotives_tradings.trade_cycles
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_trade_cycles
    BEFORE UPDATE OR DELETE ON inotives_tradings.trade_cycles
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.trade_cycles_history');


-- -----------------------------------------------------------------------------
-- 3. TRADE ORDERS
-- Every order submitted to the exchange across all strategy types.
-- Core order fields are columns; strategy-specific context (e.g. slot_number)
-- goes in metadata.
--
-- Example metadata:
--   DCA_GRID buy:  { "slot_number": 3 }
--   DCA_GRID sell: { "trigger": "take_profit", "avg_buy_price": 49200 }
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_orders (
    id          BIGSERIAL PRIMARY KEY,
    cycle_id    BIGINT NOT NULL REFERENCES inotives_tradings.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    strategy_id BIGINT NOT NULL REFERENCES inotives_tradings.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,

    exchange_order_id TEXT,  -- Order ID returned by the exchange

    side       inotives_tradings.trade_side         NOT NULL,
    order_type inotives_tradings.trade_order_type   NOT NULL DEFAULT 'LIMIT',

    -- Order intent
    target_price NUMERIC(36, 18) NOT NULL,
    quantity     NUMERIC(36, 18) NOT NULL,

    -- Fill state (updated as executions arrive)
    filled_quantity NUMERIC(36, 18) NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(36, 18),
    fee_total       NUMERIC(36, 8)  NOT NULL DEFAULT 0,
    fee_asset       TEXT,

    status       inotives_tradings.trade_order_status NOT NULL DEFAULT 'PENDING',
    submitted_at TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}',  -- Strategy-specific order context

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT uq_trade_orders_exchange_id UNIQUE (cycle_id, exchange_order_id),
    CONSTRAINT chk_deleted_fields_trade_orders CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE INDEX ON inotives_tradings.trade_orders (cycle_id, status);
CREATE INDEX ON inotives_tradings.trade_orders (cycle_id, side);

CREATE TABLE inotives_tradings.trade_orders_history (LIKE inotives_tradings.trade_orders INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.trade_orders_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.trade_orders_history (sys_period);
CREATE INDEX ON inotives_tradings.trade_orders_history (changed_at);
CREATE INDEX ON inotives_tradings.trade_orders_history (changed_by);

CREATE TRIGGER auditing_trigger_trade_orders
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_orders
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_trade_orders
    BEFORE DELETE ON inotives_tradings.trade_orders
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_trade_orders
    BEFORE UPDATE OR DELETE ON inotives_tradings.trade_orders
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.trade_orders_history');


-- -----------------------------------------------------------------------------
-- 4. TRADE EXECUTIONS
-- Immutable fill records received from the exchange.
-- One order can produce multiple partial fills.
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_executions (
    id       BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES inotives_tradings.trade_orders(id) DEFERRABLE INITIALLY DEFERRED,
    cycle_id BIGINT NOT NULL REFERENCES inotives_tradings.trade_cycles(id) DEFERRABLE INITIALLY DEFERRED,

    exchange_execution_id TEXT NOT NULL,  -- Fill ID from the exchange

    side              inotives_tradings.trade_side NOT NULL,
    executed_price    NUMERIC(36, 18) NOT NULL,
    executed_quantity NUMERIC(36, 18) NOT NULL,
    quote_quantity    NUMERIC(36, 8)  NOT NULL,  -- executed_price * executed_quantity
    fee_amount        NUMERIC(36, 8)  NOT NULL DEFAULT 0,
    fee_asset         TEXT,

    executed_at TIMESTAMPTZ NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_trade_executions UNIQUE (order_id, exchange_execution_id)
);

CREATE INDEX ON inotives_tradings.trade_executions (cycle_id, side);
CREATE INDEX ON inotives_tradings.trade_executions (executed_at);

CREATE TRIGGER auditing_trigger_trade_executions
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_executions
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();


-- -----------------------------------------------------------------------------
-- 5. TRADE PNL
-- One record written when a cycle closes. Core P&L maths are columns;
-- strategy-specific breakdown (e.g. per-slot detail) goes in metadata.
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.trade_pnl (
    id          BIGSERIAL PRIMARY KEY,
    cycle_id    BIGINT NOT NULL UNIQUE REFERENCES inotives_tradings.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    strategy_id BIGINT NOT NULL        REFERENCES inotives_tradings.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,

    -- Buy side summary
    total_buy_quantity NUMERIC(36, 18) NOT NULL,
    total_buy_cost     NUMERIC(36, 8)  NOT NULL,  -- Total quote currency spent
    avg_buy_price      NUMERIC(36, 18) NOT NULL,

    -- Sell side summary
    total_sell_quantity NUMERIC(36, 18) NOT NULL,
    total_sell_proceeds NUMERIC(36, 8)  NOT NULL,  -- Total quote currency received
    avg_sell_price      NUMERIC(36, 18) NOT NULL,

    -- Fees & P&L
    total_fees NUMERIC(36, 8) NOT NULL DEFAULT 0,
    gross_pnl  NUMERIC(36, 8) NOT NULL,  -- total_sell_proceeds - total_buy_cost
    net_pnl    NUMERIC(36, 8) NOT NULL,  -- gross_pnl - total_fees
    pnl_pct    NUMERIC(10, 6) NOT NULL,  -- net_pnl / total_buy_cost * 100

    cycle_duration_seconds BIGINT,
    closed_at  TIMESTAMPTZ NOT NULL,
    metadata   JSONB NOT NULL DEFAULT '{}',  -- Strategy-specific PnL breakdown

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE INDEX ON inotives_tradings.trade_pnl (strategy_id, closed_at DESC);

CREATE TRIGGER auditing_trigger_trade_pnl
    BEFORE INSERT OR UPDATE ON inotives_tradings.trade_pnl
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.trade_pnl CASCADE;
DROP TABLE IF EXISTS inotives_tradings.trade_executions CASCADE;
DROP TABLE IF EXISTS inotives_tradings.trade_orders_history;
DROP TABLE IF EXISTS inotives_tradings.trade_orders CASCADE;
DROP TABLE IF EXISTS inotives_tradings.trade_cycles_history;
DROP TABLE IF EXISTS inotives_tradings.trade_cycles CASCADE;
DROP TABLE IF EXISTS inotives_tradings.trade_strategies_history;
DROP TABLE IF EXISTS inotives_tradings.trade_strategies CASCADE;
DROP TYPE IF EXISTS inotives_tradings.trade_order_status;
DROP TYPE IF EXISTS inotives_tradings.trade_order_type;
DROP TYPE IF EXISTS inotives_tradings.trade_side;
DROP TYPE IF EXISTS inotives_tradings.trade_cycle_status;
DROP TYPE IF EXISTS inotives_tradings.trade_strategy_status;
