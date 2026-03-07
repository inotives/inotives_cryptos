-- migrate:up

-- -----------------------------------------------------------------------------
-- PORTFOLIO SNAPSHOTS (daily)
-- A two-table design: one header row per day summarising the full portfolio,
-- plus one position row per (snapshot × venue × asset) for per-holding detail.
--
-- Written once per day by an automated job (e.g. after market close UTC).
-- Append-only — rows are never updated after creation.
--
-- Why columns instead of metadata for positions:
--   Querying "BTC value over 30 days" or "unrealized PnL trend per venue"
--   requires indexed columns, not JSONB extraction.
-- -----------------------------------------------------------------------------

-- 1. HEADER — one row per day
CREATE TABLE base.portfolio_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL UNIQUE,  -- UTC date of the snapshot

    -- Totals across all venues and assets
    total_value_usd          NUMERIC(36, 2) NOT NULL,  -- Sum of all position values
    total_cost_basis_usd     NUMERIC(36, 2),            -- What was paid for everything held
    unrealized_pnl_usd       NUMERIC(36, 2),            -- total_value - total_cost_basis
    realized_pnl_cumulative  NUMERIC(36, 2),            -- Sum of all trade_pnl.net_pnl to date

    metadata   JSONB        NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT current_timestamp
);

CREATE INDEX ON base.portfolio_snapshots (snapshot_date DESC);


-- 2. POSITIONS — one row per (snapshot × venue × asset)
CREATE TABLE base.portfolio_snapshot_positions (
    id          BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES base.portfolio_snapshots(id) DEFERRABLE INITIALLY DEFERRED,
    venue_id    BIGINT NOT NULL REFERENCES base.venues(id)              DEFERRABLE INITIALLY DEFERRED,
    asset_id    BIGINT NOT NULL REFERENCES base.assets(id)              DEFERRABLE INITIALLY DEFERRED,

    balance       NUMERIC(36, 18) NOT NULL,  -- Units held at snapshot time
    price_usd     NUMERIC(36, 18),           -- Asset price in USD at snapshot time
    value_usd     NUMERIC(36, 2),            -- balance × price_usd
    cost_basis_usd NUMERIC(36, 2),           -- Acquisition cost for this holding

    metadata   JSONB        NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_portfolio_snapshot_positions UNIQUE (snapshot_id, venue_id, asset_id)
);

-- Trend query: value of a specific asset over time across all venues
CREATE INDEX ON base.portfolio_snapshot_positions (asset_id, snapshot_id DESC);

-- Venue drill-down: all positions at a specific venue over time
CREATE INDEX ON base.portfolio_snapshot_positions (venue_id, snapshot_id DESC);


-- migrate:down
DROP TABLE IF EXISTS base.portfolio_snapshot_positions CASCADE;
DROP TABLE IF EXISTS base.portfolio_snapshots CASCADE;
