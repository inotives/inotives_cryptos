-- migrate:up

-- Raw asset platforms from GET /asset_platforms
-- Each row is one blockchain/network as recognised by CoinGecko.
CREATE TABLE coingecko.raw_platforms (
    coingecko_id        VARCHAR(200)    NOT NULL,
    chain_identifier    INTEGER,                        -- EVM chain ID (NULL for non-EVM)
    name                TEXT            NOT NULL,
    shortname           VARCHAR(50),
    native_coin_id      VARCHAR(200),                   -- CoinGecko coin ID of the native token
    image_thumb         TEXT,
    image_small         TEXT,
    image_large         TEXT,
    fetched_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_coingecko_raw_platforms PRIMARY KEY (coingecko_id)
);

COMMENT ON TABLE  coingecko.raw_platforms                    IS 'Raw asset platforms synced from CoinGecko /asset_platforms. One row per platform.';
COMMENT ON COLUMN coingecko.raw_platforms.coingecko_id       IS 'CoinGecko platform identifier, e.g. "ethereum", "binance-smart-chain".';
COMMENT ON COLUMN coingecko.raw_platforms.chain_identifier   IS 'EVM chain ID (e.g. 1 for Ethereum mainnet). NULL for non-EVM chains.';
COMMENT ON COLUMN coingecko.raw_platforms.native_coin_id     IS 'CoinGecko coin ID of the native gas token, e.g. "ethereum".';

-- migrate:down

DROP TABLE IF EXISTS coingecko.raw_platforms CASCADE;
