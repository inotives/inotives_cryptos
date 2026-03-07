-- migrate:up

-- Intraday OHLCV candles for assets captured from external data sources.
-- Covers sub-daily timeframes: 1m, 5m, 15m, 30m, 1h, 4h.
-- Used by the trader bot for signal generation and trend analysis.
-- Append-only time-series — no soft delete or versioning needed.
--
-- Separate from asset_metrics_1d which carries extended market metrics
-- (dominance, FDV, supply data) only meaningful at daily granularity.
CREATE TABLE base.asset_metrics_intraday (
    id          BIGSERIAL PRIMARY KEY,
    asset_id    BIGINT NOT NULL REFERENCES base.assets(id)       DEFERRABLE INITIALLY DEFERRED,
    source_id   BIGINT NOT NULL REFERENCES base.data_sources(id) DEFERRABLE INITIALLY DEFERRED,
    timeframe   TEXT   NOT NULL,             -- '1m' | '5m' | '15m' | '30m' | '1h' | '4h'
    candle_time TIMESTAMPTZ NOT NULL,        -- Start of the candle period (UTC)

    -- OHLCV
    open_price  NUMERIC(36, 18) NOT NULL,
    high_price  NUMERIC(36, 18) NOT NULL,
    low_price   NUMERIC(36, 18) NOT NULL,
    close_price NUMERIC(36, 18) NOT NULL,
    volume      NUMERIC(36, 8)  NOT NULL,    -- Base asset volume
    volume_usd  NUMERIC(36, 2),             -- Quote volume in USD (if available)
    trade_count INTEGER,                    -- Number of trades in the period (if available)

    -- Record state
    is_closed   BOOLEAN NOT NULL DEFAULT true,  -- false if candle is still forming (current period)
    metadata    JSONB   NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_asset_metrics_intraday UNIQUE (asset_id, source_id, timeframe, candle_time)
);

-- Primary query pattern: latest N candles for a pair/timeframe (signal generation)
CREATE INDEX ON base.asset_metrics_intraday (asset_id, source_id, timeframe, candle_time DESC);

-- Cross-asset queries at a specific time (market snapshot)
CREATE INDEX ON base.asset_metrics_intraday (source_id, timeframe, candle_time DESC);

CREATE TRIGGER auditing_trigger_asset_metrics_intraday
    BEFORE INSERT OR UPDATE ON base.asset_metrics_intraday
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS base.asset_metrics_intraday CASCADE;
