-- migrate:up

-- Create the Table
CREATE TABLE inotives_tradings.assets (
    id               BIGSERIAL PRIMARY KEY,
    code             TEXT NOT NULL UNIQUE,          -- ex: 'eth', 'eth_C76d.fantom', 'usdc'
    name             TEXT NOT NULL,                 -- ex: 'Ethereum', 'USD Coin'
    symbol           TEXT NOT NULL,                 -- ex: 'ETH', 'USDC'
    type             TEXT NOT NULL,                 -- ex: 'crypto', 'fiat', 'equity'

    -- Network deployment fields
    network_id       BIGINT REFERENCES inotives_tradings.networks(id) DEFERRABLE INITIALLY DEFERRED,
    contract_address TEXT,                           -- NULL for native/fee-paying assets
    decimals         INTEGER,                        -- ex: 18 for ETH, 6 for USDC
    is_fee_paying    BOOLEAN NOT NULL DEFAULT false, -- true if this is the gas token of its network
    is_origin_asset  BOOLEAN NOT NULL DEFAULT false, -- true if this is the original deployment (not bridged/wrapped)

    -- Relationship fields
    canonical_asset_id BIGINT REFERENCES inotives_tradings.assets(id) DEFERRABLE INITIALLY DEFERRED, -- points to origin deployment (eth_on_base -> eth)
    backing_asset_id   BIGINT REFERENCES inotives_tradings.assets(id) DEFERRABLE INITIALLY DEFERRED, -- what backs/wraps this asset (weth -> eth)

    metadata         JSONB NOT NULL DEFAULT '{}',

    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership references
    created_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_decimals_required_for_deployments CHECK (
        network_id IS NULL OR decimals IS NOT NULL
    ),

    CONSTRAINT chk_deleted_fields_assets CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- No duplicate contracts per network (NULL-safe)
CREATE UNIQUE INDEX uq_assets_network_contract
    ON inotives_tradings.assets (network_id, contract_address)
    WHERE network_id IS NOT NULL AND contract_address IS NOT NULL;

-- History table
CREATE TABLE inotives_tradings.assets_history (LIKE inotives_tradings.assets INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.assets_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.assets_history (sys_period);
CREATE INDEX ON inotives_tradings.assets_history (changed_at);
CREATE INDEX ON inotives_tradings.assets_history (changed_by);

-- Triggers
CREATE TRIGGER auditing_trigger_assets
    BEFORE INSERT OR UPDATE ON inotives_tradings.assets
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_assets
    BEFORE DELETE ON inotives_tradings.assets
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_assets
    BEFORE UPDATE OR DELETE ON inotives_tradings.assets
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.assets_history');

-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.assets_history;
DROP TABLE IF EXISTS inotives_tradings.assets CASCADE;
