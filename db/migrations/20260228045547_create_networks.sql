-- migrate:up

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'network_category') THEN
        CREATE TYPE base.network_category AS ENUM ('legacy', 'blockchain');
    END IF;
END $$;

-- Create the Table
CREATE TABLE base.networks (
    -- code is now the Natural Primary Key
    id BIGSERIAL PRIMARY KEY,           -- Surrogate PK for internal use and FK references
    code TEXT NOT NULL UNIQUE,          -- ex: 'btc', 'eth', 'sol'
    name TEXT NOT NULL,                 -- ex: 'Bitcoin', 'Ethereum'
    category base.network_category NOT NULL, -- ex: 'legacy', 'blockchain'
    metadata JSONB DEFAULT '{}' NOT NULL, -- Flexible field for chain-specific data (e.g. chain_id for EVM chains)

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields (References remain BIGINT because Users still uses BIGINT ID)
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_networks CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- History table (mirrors main table + change metadata columns)
CREATE TABLE base.networks_history (LIKE base.networks INCLUDING DEFAULTS);
ALTER TABLE base.networks_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.networks_history (sys_period);
CREATE INDEX ON base.networks_history (changed_at);
CREATE INDEX ON base.networks_history (changed_by);

-- Triggers (Auditing, Soft Delete, Versioning)
CREATE TRIGGER auditing_trigger_networks
    BEFORE INSERT OR UPDATE ON base.networks
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_networks
    BEFORE DELETE ON base.networks
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_networks
    BEFORE UPDATE OR DELETE ON base.networks
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.networks_history');

-- migrate:down
DROP TABLE IF EXISTS base.networks_history;
DROP TABLE IF EXISTS base.networks CASCADE;
DROP TYPE IF EXISTS base.network_category;
