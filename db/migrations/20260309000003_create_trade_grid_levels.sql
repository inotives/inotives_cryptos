-- migrate:up

-- -----------------------------------------------------------------------------
-- TRADE GRID LEVELS
-- One row per grid level per cycle. Created at cycle open using ATR-derived
-- spacing and weighted capital allocation. Updated as orders are placed and
-- fills arrive.
--
-- Replaces the slot_number convention previously stored in trade_orders.metadata.
-- The bot should link each order back here via order_id once placed.
--
-- Status transitions:
--   PENDING → OPEN      (limit order submitted to exchange)
--   OPEN    → FILLED    (order fully filled)
--   OPEN    → CANCELLED (order cancelled; e.g. cycle closing early)
--   PENDING → CANCELLED (level skipped on cycle close before order was placed)
-- -----------------------------------------------------------------------------
CREATE TABLE base.trade_grid_levels (
    id          BIGSERIAL PRIMARY KEY,
    cycle_id    BIGINT NOT NULL REFERENCES base.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    strategy_id BIGINT NOT NULL REFERENCES base.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,

    -- Grid position
    level_num   INTEGER NOT NULL,  -- 1 = nearest to market price, N = deepest level

    -- ATR-derived level parameters (captured at cycle open — immutable after creation)
    target_price      NUMERIC(36, 18) NOT NULL,  -- Limit order price for this level
    weight            NUMERIC(10,  6) NOT NULL,  -- Relative allocation weight (e.g. 1.0, 2.0, 3.0)
    capital_allocated NUMERIC(36,  8) NOT NULL,  -- Quote currency to spend at this level
    quantity          NUMERIC(36, 18) NOT NULL,  -- capital_allocated / target_price

    -- Order linkage (set once the limit order is submitted)
    order_id   BIGINT REFERENCES base.trade_orders(id) DEFERRABLE INITIALLY DEFERRED,

    -- Lifecycle
    status    TEXT        NOT NULL DEFAULT 'PENDING',  -- PENDING | OPEN | FILLED | CANCELLED
    filled_at TIMESTAMPTZ,

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_trade_grid_levels        UNIQUE (cycle_id, level_num),
    CONSTRAINT chk_trade_grid_level_status CHECK  (status IN ('PENDING', 'OPEN', 'FILLED', 'CANCELLED')),
    CONSTRAINT chk_trade_grid_filled_at    CHECK  (
        (status = 'FILLED' AND filled_at IS NOT NULL) OR
        (status <> 'FILLED' AND filled_at IS NULL)
    )
);

-- Primary bot query: all levels for an active cycle ordered by price
CREATE INDEX ON base.trade_grid_levels (cycle_id, level_num);

-- Filter by status to find open/pending levels quickly
CREATE INDEX ON base.trade_grid_levels (cycle_id, status);

CREATE TRIGGER auditing_trigger_trade_grid_levels
    BEFORE INSERT OR UPDATE ON base.trade_grid_levels
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS base.trade_grid_levels CASCADE;
