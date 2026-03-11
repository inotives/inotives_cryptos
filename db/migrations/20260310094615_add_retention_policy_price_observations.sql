-- migrate:up

-- Enable TimescaleDB extension (preloaded via shared_preload_libraries).
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- TimescaleDB requires ALL unique indexes (including PK) to contain the partition column.
-- The current PK is (id) alone, so we recreate it as (id, observed_at).
-- The existing UNIQUE (source_id, base_asset_id, quote_asset_id, observed_at) already qualifies.
ALTER TABLE base.price_observations DROP CONSTRAINT price_observations_pkey;
ALTER TABLE base.price_observations ADD PRIMARY KEY (id, observed_at);

-- Convert to hypertable partitioned by observed_at with 1-day chunks.
-- migrate_data=TRUE preserves existing rows.
SELECT create_hypertable(
    'base.price_observations',
    'observed_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- Automatically drop chunks older than 90 days.
-- At 1-minute polling across 4 pairs this caps storage at ~180 MB uncompressed / ~18 MB compressed.
SELECT add_retention_policy(
    'base.price_observations',
    INTERVAL '90 days',
    if_not_exists => TRUE
);


-- migrate:down

SELECT remove_retention_policy('base.price_observations', if_not_exists => TRUE);

-- Restore single-column primary key.
-- Note: TimescaleDB does not support converting a hypertable back to a plain table.
ALTER TABLE base.price_observations DROP CONSTRAINT price_observations_pkey;
ALTER TABLE base.price_observations ADD PRIMARY KEY (id);
