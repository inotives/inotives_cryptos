-- migrate:up

-- Create the Table
CREATE TABLE base.assets (
    id               BIGSERIAL PRIMARY KEY,
    code             TEXT NOT NULL UNIQUE,          -- ex: 'btc', 'usdc', 'eth'
    name             TEXT NOT NULL,                 -- ex: 'Bitcoin', 'USD Coin'
    type             TEXT NOT NULL,                 -- ex: 'native', 'token', 'stablecoin', 'fiat'
    origin_network_id BIGINT REFERENCES base.networks(id) DEFERRABLE INITIALLY DEFERRED,
    backing_asset_id  BIGINT REFERENCES base.assets(id)   DEFERRABLE INITIALLY DEFERRED,
    metadata         JSONB NOT NULL DEFAULT '{}',   -- Flexible field for asset-specific data

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

    CONSTRAINT chk_deleted_fields_assets CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- History table (mirrors main table + change metadata columns)
CREATE TABLE base.assets_history (LIKE base.assets INCLUDING DEFAULTS);
ALTER TABLE base.assets_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.assets_history (sys_period);
CREATE INDEX ON base.assets_history (changed_at);
CREATE INDEX ON base.assets_history (changed_by);

-- Triggers (Auditing, Soft Delete, Versioning)
CREATE TRIGGER auditing_trigger_assets
    BEFORE INSERT OR UPDATE ON base.assets
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_assets
    BEFORE DELETE ON base.assets
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_assets
    BEFORE UPDATE OR DELETE ON base.assets
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.assets_history');

-- migrate:down
DROP TABLE IF EXISTS base.assets_history;
DROP TABLE IF EXISTS base.assets CASCADE;
