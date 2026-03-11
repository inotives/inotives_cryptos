-- migrate:up

-- Create dedicated schema for raw CoinGecko ingestion data
CREATE SCHEMA IF NOT EXISTS coingecko;

-- Raw coin list from GET /coins/list?include_platform=true
-- Upserted on each sync run. Serves as the authoritative CoinGecko ID ↔ symbol/name lookup.
CREATE TABLE coingecko.raw_coins (
    coingecko_id    VARCHAR(200)    NOT NULL,
    symbol          VARCHAR(50)     NOT NULL,
    name            TEXT            NOT NULL,
    platforms       JSONB           NOT NULL DEFAULT '{}',
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_coingecko_raw_coins PRIMARY KEY (coingecko_id)
);

COMMENT ON TABLE  coingecko.raw_coins                IS 'Raw coin list synced from CoinGecko /coins/list. One row per CoinGecko coin ID.';
COMMENT ON COLUMN coingecko.raw_coins.coingecko_id   IS 'CoinGecko coin identifier, e.g. "bitcoin", "ethereum".';
COMMENT ON COLUMN coingecko.raw_coins.symbol         IS 'Ticker symbol as reported by CoinGecko, e.g. "btc".';
COMMENT ON COLUMN coingecko.raw_coins.name           IS 'Human-readable name, e.g. "Bitcoin".';
COMMENT ON COLUMN coingecko.raw_coins.platforms      IS 'Map of chain → contract address from CoinGecko, e.g. {"ethereum": "0x..."}.';
COMMENT ON COLUMN coingecko.raw_coins.fetched_at     IS 'Timestamp of the API call that produced this row.';

CREATE INDEX idx_coingecko_raw_coins_symbol ON coingecko.raw_coins (symbol);

-- migrate:down

DROP TABLE IF EXISTS coingecko.raw_coins CASCADE;
DROP SCHEMA IF EXISTS coingecko CASCADE;
