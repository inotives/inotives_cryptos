-- migrate:up

-- Add 24h quote volume captured from the exchange ticker.
-- Nullable because some exchanges don't provide it on public endpoints
-- (approximated from baseVolume * last in the pricing bot).
ALTER TABLE inotives_tradings.price_observations
    ADD COLUMN volume_24h NUMERIC(36, 2);


-- migrate:down

ALTER TABLE inotives_tradings.price_observations
    DROP COLUMN IF EXISTS volume_24h;
