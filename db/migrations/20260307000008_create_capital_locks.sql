-- migrate:up

-- -----------------------------------------------------------------------------
-- CAPITAL LOCKS
-- Tracks how much capital is reserved by each active trade cycle at a venue.
-- Created when a cycle opens; released when it closes.
--
-- Prevents the trader bot from over-allocating capital across concurrent cycles.
--
-- Available capital (bot pre-check query):
--   SELECT available_balance
--   FROM base.venue_available_capital
--   WHERE venue_id = $1 AND asset_id = $2;
-- -----------------------------------------------------------------------------
CREATE TABLE base.capital_locks (
    id          BIGSERIAL PRIMARY KEY,
    venue_id    BIGINT NOT NULL REFERENCES base.venues(id)          DEFERRABLE INITIALLY DEFERRED,
    asset_id    BIGINT NOT NULL REFERENCES base.assets(id)          DEFERRABLE INITIALLY DEFERRED,
    cycle_id    BIGINT NOT NULL UNIQUE REFERENCES base.trade_cycles(id)     DEFERRABLE INITIALLY DEFERRED,
    strategy_id BIGINT NOT NULL REFERENCES base.trade_strategies(id) DEFERRABLE INITIALLY DEFERRED,

    amount      NUMERIC(36, 8) NOT NULL,  -- = trade_cycles.capital_allocated at cycle open

    status      TEXT NOT NULL DEFAULT 'ACTIVE',  -- 'ACTIVE' | 'RELEASED'
    locked_at   TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    released_at TIMESTAMPTZ,  -- set when cycle closes

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT chk_capital_lock_status CHECK (status IN ('ACTIVE', 'RELEASED')),
    CONSTRAINT chk_capital_lock_release CHECK (
        (status = 'ACTIVE'   AND released_at IS NULL) OR
        (status = 'RELEASED' AND released_at IS NOT NULL)
    )
);

-- Primary bot query: available capital at a venue for a given asset
CREATE INDEX ON base.capital_locks (venue_id, asset_id, status);
CREATE INDEX ON base.capital_locks (strategy_id);

CREATE TRIGGER auditing_trigger_capital_locks
    BEFORE INSERT OR UPDATE ON base.capital_locks
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();


-- -----------------------------------------------------------------------------
-- VIEW: venue_available_capital
-- Single query for the trader bot to check how much capital is free to deploy.
--
-- Usage:
--   SELECT total_balance, locked_amount, available_balance
--   FROM base.venue_available_capital
--   WHERE venue_id = $1 AND asset_id = $2;
-- -----------------------------------------------------------------------------
CREATE VIEW base.venue_available_capital AS
SELECT
    vb.venue_id,
    vb.asset_id,
    vb.balance                                                              AS total_balance,
    COALESCE(SUM(cl.amount) FILTER (WHERE cl.status = 'ACTIVE'), 0)        AS locked_amount,
    vb.balance - COALESCE(SUM(cl.amount) FILTER (WHERE cl.status = 'ACTIVE'), 0) AS available_balance
FROM base.venue_balances vb
LEFT JOIN base.capital_locks cl
       ON cl.venue_id = vb.venue_id
      AND cl.asset_id = vb.asset_id
WHERE vb.deleted_at IS NULL
GROUP BY vb.venue_id, vb.asset_id, vb.balance;


-- migrate:down
DROP VIEW  IF EXISTS base.venue_available_capital;
DROP TABLE IF EXISTS base.capital_locks CASCADE;
