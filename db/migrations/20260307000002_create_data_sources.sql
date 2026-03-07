-- migrate:up

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'data_source_category') THEN
        CREATE TYPE base.data_source_category AS ENUM ('MARKET_DATA', 'CUSTODY', 'ONCHAIN', 'EXECUTION');
    END IF;
END $$;

-- Create the Table
CREATE TABLE base.data_sources (
    id              BIGSERIAL PRIMARY KEY,
    source_code     TEXT NOT NULL UNIQUE,
    provider_name   TEXT NOT NULL,
    category        base.data_source_category NOT NULL,
    tier_name       TEXT    NOT NULL DEFAULT 'free',
    rate_limit_rpm  INTEGER NOT NULL DEFAULT 30,
    site_url        TEXT,
    metadata        JSONB   NOT NULL DEFAULT '{}',

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

    CONSTRAINT chk_deleted_fields_data_sources CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);

-- History table (mirrors main table + change metadata columns)
CREATE TABLE base.data_sources_history (LIKE base.data_sources INCLUDING DEFAULTS);
ALTER TABLE base.data_sources_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.data_sources_history (sys_period);
CREATE INDEX ON base.data_sources_history (changed_at);
CREATE INDEX ON base.data_sources_history (changed_by);

-- Triggers (Auditing, Soft Delete, Versioning)
CREATE TRIGGER auditing_trigger_data_sources
    BEFORE INSERT OR UPDATE ON base.data_sources
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

CREATE TRIGGER soft_delete_trigger_data_sources
    BEFORE DELETE ON base.data_sources
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

CREATE TRIGGER versioning_trigger_data_sources
    BEFORE UPDATE OR DELETE ON base.data_sources
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.data_sources_history');

-- migrate:down
DROP TABLE IF EXISTS base.data_sources_history;
DROP TABLE IF EXISTS base.data_sources CASCADE;
DROP TYPE IF EXISTS base.data_source_category;
