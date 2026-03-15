-- migrate:up

-- ENUMs
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'venue_type') THEN
        CREATE TYPE inotives_tradings.venue_type AS ENUM ('CEFI_EXCHANGE', 'DEFI_WALLET', 'HARDWARE_WALLET', 'SMART_CONTRACT', 'CUSTODIAN');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'transfer_type') THEN
        CREATE TYPE inotives_tradings.transfer_type AS ENUM ('DEPOSIT', 'WITHDRAWAL', 'INTERNAL');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'transfer_status') THEN
        CREATE TYPE inotives_tradings.transfer_status AS ENUM ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED');
    END IF;
END $$;


-- -----------------------------------------------------------------------------
-- 1. VENUES
-- Represents any account, wallet, or profile you own — CeFi exchange accounts,
-- DeFi wallets, hardware wallets, smart contract wallets, custodians, etc.
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.venues (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    venue_type  inotives_tradings.venue_type NOT NULL,

    -- Where this venue lives
    source_id  BIGINT REFERENCES inotives_tradings.data_sources(id) DEFERRABLE INITIALLY DEFERRED,  -- NULL for self-hosted wallets
    network_id BIGINT REFERENCES inotives_tradings.networks(id)     DEFERRABLE INITIALLY DEFERRED,  -- NULL for CeFi

    -- Identifier on the exchange or chain
    address TEXT,  -- Wallet address for DeFi; account ID/label for CeFi

    metadata JSONB NOT NULL DEFAULT '{}',

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

    CONSTRAINT chk_deleted_fields_venues CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE INDEX ON inotives_tradings.venues (venue_type);
CREATE INDEX ON inotives_tradings.venues (source_id);

CREATE TABLE inotives_tradings.venues_history (LIKE inotives_tradings.venues INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.venues_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.venues_history (sys_period);
CREATE INDEX ON inotives_tradings.venues_history (changed_at);
CREATE INDEX ON inotives_tradings.venues_history (changed_by);

CREATE TRIGGER auditing_trigger_venues
    BEFORE INSERT OR UPDATE ON inotives_tradings.venues
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_venues
    BEFORE DELETE ON inotives_tradings.venues
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_venues
    BEFORE UPDATE OR DELETE ON inotives_tradings.venues
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.venues_history');


-- -----------------------------------------------------------------------------
-- 2. VENUE BALANCES
-- Current asset balance at each venue. One row per asset per venue.
-- A venue with BTC, ETH, USDC would have 3 rows.
--
-- For CeFi: asset_id is enough; network_asset_id is NULL.
-- For DeFi: network_asset_id pins the exact contract (USDC on ETH vs USDC on Polygon).
--
-- Versioning history = full balance audit trail for reconciliation.
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.venue_balances (
    id              BIGSERIAL PRIMARY KEY,
    venue_id        BIGINT NOT NULL REFERENCES inotives_tradings.venues(id)         DEFERRABLE INITIALLY DEFERRED,
    asset_id        BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)         DEFERRABLE INITIALLY DEFERRED,
    network_asset_id BIGINT REFERENCES inotives_tradings.assets(id)                 DEFERRABLE INITIALLY DEFERRED,  -- NULL for CeFi; points to network-specific asset row

    balance      NUMERIC(36, 18) NOT NULL DEFAULT 0,
    balance_usd  NUMERIC(36, 2),          -- USD value at last sync (nullable)
    last_synced_at TIMESTAMPTZ NOT NULL,  -- When the balance was last fetched

    metadata JSONB NOT NULL DEFAULT '{}',

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

    CONSTRAINT chk_deleted_fields_venue_balances CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- CeFi: one balance row per (venue, asset)
CREATE UNIQUE INDEX uq_venue_balances_cefi
    ON inotives_tradings.venue_balances (venue_id, asset_id)
    WHERE network_asset_id IS NULL;

-- DeFi: one balance row per (venue, network_asset) — contract-level precision
CREATE UNIQUE INDEX uq_venue_balances_defi
    ON inotives_tradings.venue_balances (venue_id, network_asset_id)
    WHERE network_asset_id IS NOT NULL;

CREATE INDEX ON inotives_tradings.venue_balances (venue_id, asset_id);

CREATE TABLE inotives_tradings.venue_balances_history (LIKE inotives_tradings.venue_balances INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.venue_balances_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.venue_balances_history (sys_period);
CREATE INDEX ON inotives_tradings.venue_balances_history (changed_at);
CREATE INDEX ON inotives_tradings.venue_balances_history (changed_by);

CREATE TRIGGER auditing_trigger_venue_balances
    BEFORE INSERT OR UPDATE ON inotives_tradings.venue_balances
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_venue_balances
    BEFORE DELETE ON inotives_tradings.venue_balances
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_venue_balances
    BEFORE UPDATE OR DELETE ON inotives_tradings.venue_balances
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.venue_balances_history');


-- -----------------------------------------------------------------------------
-- 3. TRANSFERS
-- Deposits, withdrawals, and internal moves between venues.
-- Critical for audit and reconciliation against venue balances.
--
-- DEPOSIT:   from_venue_id = NULL  (funds arrived from outside)
-- WITHDRAWAL: to_venue_id  = NULL  (funds left to outside)
-- INTERNAL:  both set              (rebalancing between own venues)
-- -----------------------------------------------------------------------------
CREATE TABLE inotives_tradings.transfers (
    id            BIGSERIAL PRIMARY KEY,
    transfer_type inotives_tradings.transfer_type   NOT NULL,
    status        inotives_tradings.transfer_status NOT NULL DEFAULT 'PENDING',

    from_venue_id BIGINT REFERENCES inotives_tradings.venues(id) DEFERRABLE INITIALLY DEFERRED,  -- NULL for external deposits
    to_venue_id   BIGINT REFERENCES inotives_tradings.venues(id) DEFERRABLE INITIALLY DEFERRED,  -- NULL for external withdrawals

    asset_id      BIGINT NOT NULL REFERENCES inotives_tradings.assets(id)    DEFERRABLE INITIALLY DEFERRED,
    network_id    BIGINT REFERENCES inotives_tradings.networks(id)           DEFERRABLE INITIALLY DEFERRED,  -- Chain the transfer moved on
    amount        NUMERIC(36, 18) NOT NULL,

    -- Fees
    fee_amount   NUMERIC(36, 8) NOT NULL DEFAULT 0,
    fee_asset_id BIGINT REFERENCES inotives_tradings.assets(id) DEFERRABLE INITIALLY DEFERRED,

    -- External identifiers
    tx_hash         TEXT,  -- On-chain transaction hash
    exchange_tx_id  TEXT,  -- CeFi exchange transaction ID

    initiated_at  TIMESTAMPTZ NOT NULL,
    confirmed_at  TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}',

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

    CONSTRAINT chk_transfer_venues CHECK (
        from_venue_id IS NOT NULL OR to_venue_id IS NOT NULL
    ),
    CONSTRAINT chk_deleted_fields_transfers CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

CREATE INDEX ON inotives_tradings.transfers (from_venue_id, initiated_at DESC);
CREATE INDEX ON inotives_tradings.transfers (to_venue_id, initiated_at DESC);
CREATE INDEX ON inotives_tradings.transfers (asset_id, initiated_at DESC);
CREATE INDEX ON inotives_tradings.transfers (tx_hash) WHERE tx_hash IS NOT NULL;
CREATE INDEX ON inotives_tradings.transfers (status);

CREATE TABLE inotives_tradings.transfers_history (LIKE inotives_tradings.transfers INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.transfers_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.transfers_history (sys_period);
CREATE INDEX ON inotives_tradings.transfers_history (changed_at);
CREATE INDEX ON inotives_tradings.transfers_history (changed_by);

CREATE TRIGGER auditing_trigger_transfers
    BEFORE INSERT OR UPDATE ON inotives_tradings.transfers
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_transfers
    BEFORE DELETE ON inotives_tradings.transfers
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_transfers
    BEFORE UPDATE OR DELETE ON inotives_tradings.transfers
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.transfers_history');


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.transfers_history;
DROP TABLE IF EXISTS inotives_tradings.transfers CASCADE;
DROP TABLE IF EXISTS inotives_tradings.venue_balances_history;
DROP TABLE IF EXISTS inotives_tradings.venue_balances CASCADE;
DROP TABLE IF EXISTS inotives_tradings.venues_history;
DROP TABLE IF EXISTS inotives_tradings.venues CASCADE;
DROP TYPE IF EXISTS inotives_tradings.transfer_status;
DROP TYPE IF EXISTS inotives_tradings.transfer_type;
DROP TYPE IF EXISTS inotives_tradings.venue_type;
