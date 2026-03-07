-- migrate:up

-- Create the Table
CREATE TABLE base.network_assets (
    id               BIGSERIAL PRIMARY KEY,
    network_id       BIGINT NOT NULL REFERENCES base.networks(id) DEFERRABLE INITIALLY DEFERRED,
    asset_id         BIGINT NOT NULL REFERENCES base.assets(id)   DEFERRABLE INITIALLY DEFERRED,
    root_asset_id    BIGINT          REFERENCES base.assets(id)   DEFERRABLE INITIALLY DEFERRED,
    contract_address TEXT,                                         -- NULL for native assets
    is_fee_paying    BOOLEAN  NOT NULL DEFAULT false,
    decimals         INTEGER  NOT NULL,
    metadata         JSONB    NOT NULL DEFAULT '{}',

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

    CONSTRAINT chk_deleted_fields_network_assets CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- History table (mirrors main table + change metadata columns)
CREATE TABLE base.network_assets_history (LIKE base.network_assets INCLUDING DEFAULTS);
ALTER TABLE base.network_assets_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
-- Partial unique indexes for contract_address uniqueness (handles NULL correctly)
CREATE UNIQUE INDEX uq_network_assets_contract
    ON base.network_assets (network_id, asset_id, contract_address)
    WHERE contract_address IS NOT NULL;

CREATE UNIQUE INDEX uq_network_assets_native
    ON base.network_assets (network_id, asset_id)
    WHERE contract_address IS NULL;

CREATE INDEX ON base.network_assets_history (sys_period);
CREATE INDEX ON base.network_assets_history (changed_at);
CREATE INDEX ON base.network_assets_history (changed_by);

-- Triggers (Auditing, Soft Delete, Versioning)
CREATE TRIGGER auditing_trigger_network_assets
    BEFORE INSERT OR UPDATE ON base.network_assets
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_network_assets
    BEFORE DELETE ON base.network_assets
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_network_assets
    BEFORE UPDATE OR DELETE ON base.network_assets
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.network_assets_history');

-- migrate:down
DROP TABLE IF EXISTS base.network_assets_history;
DROP TABLE IF EXISTS base.network_assets CASCADE;
