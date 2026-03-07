-- migrate:up

-- Translates internal assets to the identifiers used by each external data source.
-- e.g. CoinMarketCap identifies Bitcoin as id="1", CoinGecko as slug="bitcoin".
CREATE TABLE base.asset_source_mappings (
    id                BIGSERIAL PRIMARY KEY,
    asset_id          BIGINT NOT NULL REFERENCES base.assets(id)       DEFERRABLE INITIALLY DEFERRED,
    source_id         BIGINT NOT NULL REFERENCES base.data_sources(id) DEFERRABLE INITIALLY DEFERRED,
    source_identifier TEXT   NOT NULL,  -- Primary key the source uses (numeric ID, slug, etc.)
    source_symbol     TEXT,             -- Ticker/symbol as the source labels it
    source_name       TEXT,             -- Full name as the source calls it
    metadata          JSONB  NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT uq_asset_source_mappings UNIQUE (asset_id, source_id),
    CONSTRAINT chk_deleted_fields_asset_source_mappings CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE TABLE base.asset_source_mappings_history (LIKE base.asset_source_mappings INCLUDING DEFAULTS);
ALTER TABLE base.asset_source_mappings_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.asset_source_mappings_history (sys_period);
CREATE INDEX ON base.asset_source_mappings_history (changed_at);
CREATE INDEX ON base.asset_source_mappings_history (changed_by);

CREATE TRIGGER auditing_trigger_asset_source_mappings
    BEFORE INSERT OR UPDATE ON base.asset_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_asset_source_mappings
    BEFORE DELETE ON base.asset_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_asset_source_mappings
    BEFORE UPDATE OR DELETE ON base.asset_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.asset_source_mappings_history');


-- Translates internal networks to the identifiers used by each external data source.
-- e.g. CoinMarketCap identifies Ethereum as platform_id="1027", CoinGecko as slug="ethereum".
CREATE TABLE base.network_source_mappings (
    id                BIGSERIAL PRIMARY KEY,
    network_id        BIGINT NOT NULL REFERENCES base.networks(id)      DEFERRABLE INITIALLY DEFERRED,
    source_id         BIGINT NOT NULL REFERENCES base.data_sources(id)  DEFERRABLE INITIALLY DEFERRED,
    source_identifier TEXT   NOT NULL,  -- Primary key the source uses (numeric ID, slug, etc.)
    source_symbol     TEXT,             -- Symbol/code as the source labels it
    source_name       TEXT,             -- Full name as the source calls it
    metadata          JSONB  NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT uq_network_source_mappings UNIQUE (network_id, source_id),
    CONSTRAINT chk_deleted_fields_network_source_mappings CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE TABLE base.network_source_mappings_history (LIKE base.network_source_mappings INCLUDING DEFAULTS);
ALTER TABLE base.network_source_mappings_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.network_source_mappings_history (sys_period);
CREATE INDEX ON base.network_source_mappings_history (changed_at);
CREATE INDEX ON base.network_source_mappings_history (changed_by);

CREATE TRIGGER auditing_trigger_network_source_mappings
    BEFORE INSERT OR UPDATE ON base.network_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_network_source_mappings
    BEFORE DELETE ON base.network_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_network_source_mappings
    BEFORE UPDATE OR DELETE ON base.network_source_mappings
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.network_source_mappings_history');


-- migrate:down
DROP TABLE IF EXISTS base.network_source_mappings_history;
DROP TABLE IF EXISTS base.network_source_mappings CASCADE;
DROP TABLE IF EXISTS base.asset_source_mappings_history;
DROP TABLE IF EXISTS base.asset_source_mappings CASCADE;
