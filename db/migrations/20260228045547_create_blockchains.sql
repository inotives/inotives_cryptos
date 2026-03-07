-- migrate:up

-- Create the Table
CREATE TABLE base.blockchains (
    -- code is now the Natural Primary Key
    id BIGSERIAL PRIMARY KEY,          -- Surrogate PK for internal use and FK references
    code TEXT NOT NULL UNIQUE,          -- ex: 'btc', 'eth', 'sol'
    name TEXT NOT NULL,             -- ex: 'Bitcoin', 'Ethereum' 
    blockchain_metadata JSONB,      -- Flexible field for chain-specific data (e.g. chain_id for EVM chains)

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    
    -- Soft Delete fields (References remain BIGINT because Users still uses BIGINT ID)
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal field
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_blockchains CHECK (
        (deleted_at IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- History table
CREATE TABLE base.blockchains_history (LIKE base.blockchains INCLUDING DEFAULTS);
CREATE INDEX ON base.blockchains_history (sys_period);

-- Triggers (Auditing, Soft Delete, Versioning)
CREATE TRIGGER auditing_trigger_blockchains
    BEFORE INSERT OR UPDATE ON base.blockchains
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_blockchains
    BEFORE DELETE ON base.blockchains
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_blockchains
    BEFORE INSERT OR UPDATE OR DELETE ON base.blockchains
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.blockchains_history');

-- migrate:down
DROP TABLE IF EXISTS base.blockchains_history;
DROP TABLE IF EXISTS base.blockchains CASCADE; 