-- migrate:up

-- Operational event log for all automated bots and system processes.
-- Records what bots did, what they evaluated, and any errors encountered.
-- Append-only — no soft delete or versioning.
--
-- Examples:
--   Pricing bot:  BOT_STARTED, PRICE_FETCHED, FETCH_FAILED
--   Trader bot:   TRIGGER_EVALUATED, CYCLE_OPENED, CYCLE_CLOSED,
--                 ORDER_SUBMITTED, ORDER_FILLED, ORDER_FAILED
--   System:       BALANCE_SYNCED, CAPITAL_LOCK_CREATED, CAPITAL_LOCK_RELEASED
CREATE TABLE base.system_events (
    id         BIGSERIAL PRIMARY KEY,

    -- Source of the event
    bot_name   TEXT NOT NULL,   -- 'pricing_bot' | 'trader_bot' | 'balance_sync' | 'system'
    event_type TEXT NOT NULL,   -- e.g. 'CYCLE_OPENED', 'ORDER_FAILED', 'TRIGGER_EVALUATED'
    severity   TEXT NOT NULL DEFAULT 'INFO',  -- 'INFO' | 'WARNING' | 'ERROR'

    -- Optional links to relevant records (all nullable)
    strategy_id BIGINT REFERENCES base.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,
    cycle_id    BIGINT REFERENCES base.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    order_id    BIGINT REFERENCES base.trade_orders(id)     DEFERRABLE INITIALLY DEFERRED,
    venue_id    BIGINT REFERENCES base.venues(id)           DEFERRABLE INITIALLY DEFERRED,

    -- Event detail
    message     TEXT,           -- Human-readable description
    payload     JSONB NOT NULL DEFAULT '{}',  -- Machine-readable event data

    occurred_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT chk_system_event_severity CHECK (severity IN ('INFO', 'WARNING', 'ERROR'))
);

-- Most common query: recent events for a specific strategy or cycle
CREATE INDEX ON base.system_events (strategy_id, occurred_at DESC);
CREATE INDEX ON base.system_events (cycle_id, occurred_at DESC);

-- Ops monitoring: recent errors across all bots
CREATE INDEX ON base.system_events (severity, occurred_at DESC);

-- Log tailing: all recent events by bot
CREATE INDEX ON base.system_events (bot_name, occurred_at DESC);


-- migrate:down
DROP TABLE IF EXISTS base.system_events CASCADE;
