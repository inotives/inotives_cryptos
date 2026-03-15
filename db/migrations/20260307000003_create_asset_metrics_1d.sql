-- migrate:up

-- Daily aggregate metrics for assets captured from external data sources (e.g. CoinGecko, CoinMarketCap).
-- This is an append/upsert time-series table — no soft delete, versioning, or history table needed.
CREATE TABLE inotives_tradings.asset_metrics_1d (
    id          BIGSERIAL PRIMARY KEY,
    asset_id    BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)       DEFERRABLE INITIALLY DEFERRED,
    source_id   BIGINT NOT NULL REFERENCES inotives_tradings.data_sources(id) DEFERRABLE INITIALLY DEFERRED,
    metric_date DATE   NOT NULL,

    -- OHLCV
    open_price  NUMERIC(36, 18),
    high_price  NUMERIC(36, 18),
    low_price   NUMERIC(36, 18),
    close_price NUMERIC(36, 18),
    vwap        NUMERIC(36, 18),         -- Volume-weighted average price for the day
    volume_usd  NUMERIC(36, 2),

    -- Intraday extremes
    high_at     TIMESTAMPTZ,             -- Timestamp when daily high was reached
    low_at      TIMESTAMPTZ,             -- Timestamp when daily low was reached

    -- Price movement
    price_change_pct  NUMERIC(12, 6),    -- % change vs previous close

    -- Market cap & supply
    market_cap_usd            NUMERIC(36, 2),
    fully_diluted_valuation   NUMERIC(36, 2),  -- FDV = price * max_supply
    circulating_supply        NUMERIC(36, 8),
    total_supply              NUMERIC(36, 8),  -- Includes locked/unvested tokens

    -- Market health indicators
    dominance_percent  NUMERIC(10, 6),   -- % of total crypto market cap
    volatility_30d     NUMERIC(10, 6),   -- 30-day rolling volatility
    liquidity_score    NUMERIC(10, 6),   -- Source-defined liquidity score

    -- Record state
    is_final   BOOLEAN NOT NULL DEFAULT false,  -- false while day is still in progress
    metadata   JSONB   NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_asset_metrics_1d UNIQUE (asset_id, metric_date, source_id)
);

CREATE INDEX ON inotives_tradings.asset_metrics_1d (asset_id, metric_date DESC);
CREATE INDEX ON inotives_tradings.asset_metrics_1d (metric_date DESC);
CREATE INDEX ON inotives_tradings.asset_metrics_1d (source_id);

-- Auditing trigger only (no soft delete or versioning for time-series data)
CREATE TRIGGER auditing_trigger_asset_metrics_1d
    BEFORE INSERT OR UPDATE ON inotives_tradings.asset_metrics_1d
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.asset_metrics_1d CASCADE;
