-- migrate:up

-- price_observations stays as a regular table (TimescaleDB hypertable removed).
-- The composite PK (id, observed_at) is kept for potential future partitioning.
ALTER TABLE inotives_tradings.price_observations DROP CONSTRAINT price_observations_pkey;
ALTER TABLE inotives_tradings.price_observations ADD PRIMARY KEY (id, observed_at);


-- migrate:down

-- Restore single-column primary key.
ALTER TABLE inotives_tradings.price_observations DROP CONSTRAINT price_observations_pkey;
ALTER TABLE inotives_tradings.price_observations ADD PRIMARY KEY (id);
