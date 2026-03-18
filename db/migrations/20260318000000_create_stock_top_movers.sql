-- migrate:up

-- Top gaining and losing stocks scraped from Yahoo Finance.
-- Stores daily snapshots of market movers for analysis.
-- Append-only table — no soft delete or versioning needed.
CREATE TABLE inotives_tradings.stock_top_movers (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    price NUMERIC(36, 18),
    change_percent NUMERIC(10, 6),
    volume BIGINT,
    market_cap BIGINT,
    movers_type TEXT NOT NULL CHECK (movers_type IN ('gainer', 'loser')),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE INDEX ON inotives_tradings.stock_top_movers (fetched_at DESC);
CREATE INDEX ON inotives_tradings.stock_top_movers (symbol, fetched_at DESC);
CREATE INDEX ON inotives_tradings.stock_top_movers (movers_type, fetched_at DESC);


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.stock_top_movers CASCADE;
