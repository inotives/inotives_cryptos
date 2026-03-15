-- migrate:up

-- Periodic price snapshots captured by the pricing bot from exchanges.
-- Written every 1–5 minutes per monitored pair. Append-only time-series —
-- no soft delete or versioning needed.
--
-- The trader bot polls the latest record per (source, base_asset, quote_asset)
-- to evaluate strategy trigger conditions.
CREATE TABLE inotives_tradings.price_observations (
    id             BIGSERIAL PRIMARY KEY,
    source_id      BIGINT NOT NULL REFERENCES inotives_tradings.data_sources(id) DEFERRABLE INITIALLY DEFERRED,
    base_asset_id  BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)       DEFERRABLE INITIALLY DEFERRED,
    quote_asset_id BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)       DEFERRABLE INITIALLY DEFERRED,

    -- Price snapshot
    observed_price NUMERIC(36, 18) NOT NULL,  -- Last trade price (mid if unavailable)
    bid_price      NUMERIC(36, 18),           -- Best bid
    ask_price      NUMERIC(36, 18),           -- Best ask
    spread_pct     NUMERIC(10, 6),            -- (ask - bid) / mid * 100

    observed_at    TIMESTAMPTZ NOT NULL,      -- When the bot captured this snapshot

    metadata       JSONB NOT NULL DEFAULT '{}',  -- Extra exchange-specific fields

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_price_observations UNIQUE (source_id, base_asset_id, quote_asset_id, observed_at)
);

-- Primary query pattern: latest price for a given pair on a given exchange
CREATE INDEX ON inotives_tradings.price_observations (source_id, base_asset_id, quote_asset_id, observed_at DESC);

-- Time-range queries across all pairs
CREATE INDEX ON inotives_tradings.price_observations (observed_at DESC);


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.price_observations CASCADE;
