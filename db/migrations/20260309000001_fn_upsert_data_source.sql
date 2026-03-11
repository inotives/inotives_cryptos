-- migrate:up

-- Reusable function to insert or update a data source.
-- Idempotent: safe to call repeatedly (e.g. from seed scripts or application code).
-- Returns the id of the upserted row.
--
-- Example:
--   SELECT base.upsert_data_source(
--       'exchange:cryptocom', 'Crypto.com', 'MARKET_DATA',
--       'free', 60, 'https://crypto.com', '{}'::jsonb
--   );
CREATE OR REPLACE FUNCTION base.upsert_data_source(
    p_source_code    TEXT,
    p_provider_name  TEXT,
    p_category       TEXT,           -- must match base.data_source_category enum
    p_tier_name      TEXT    DEFAULT 'free',
    p_rate_limit_rpm INTEGER DEFAULT 30,
    p_site_url       TEXT    DEFAULT NULL,
    p_metadata       JSONB   DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO base.data_sources
        (source_code, provider_name, category, tier_name, rate_limit_rpm, site_url, metadata)
    VALUES
        (p_source_code, p_provider_name, p_category::base.data_source_category,
         p_tier_name, p_rate_limit_rpm, p_site_url, p_metadata)
    ON CONFLICT (source_code) DO UPDATE
        SET provider_name  = EXCLUDED.provider_name,
            category       = EXCLUDED.category,
            tier_name      = EXCLUDED.tier_name,
            rate_limit_rpm = EXCLUDED.rate_limit_rpm,
            site_url       = EXCLUDED.site_url,
            metadata       = EXCLUDED.metadata
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;


-- migrate:down

DROP FUNCTION IF EXISTS base.upsert_data_source(TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB);
