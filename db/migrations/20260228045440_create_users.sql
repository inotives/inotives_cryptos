-- migrate:up

-- 1. Create the ENUM type

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_status') THEN
        CREATE TYPE inotives_tradings.user_status AS ENUM ('active', 'suspend', 'deleted', 'pending');
    END IF;
END $$;


-- 2. Create base table
CREATE TABLE inotives_tradings.users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',  -- will create a seperate table for roles and permissions in future, for now keep in simple. 
    status inotives_tradings.user_status NOT NULL DEFAULT 'active',
    metadata JSONB DEFAULT '{}' NOT NULL, -- Flexible field for some use details.
    
    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    
    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal / versioning fields
    version    INTEGER   NOT NULL DEFAULT 1,
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership/Audit references
    created_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES inotives_tradings.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_users CHECK (
        (deleted_at is null and deleted_by is null)
        or (deleted_at is not null and deleted_by is not null)
    )
);

-- 3. Create history table (mirrors main table + change metadata columns)
CREATE TABLE inotives_tradings.users_history (LIKE inotives_tradings.users INCLUDING DEFAULTS);
ALTER TABLE inotives_tradings.users_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON inotives_tradings.users_history (sys_period);
CREATE INDEX ON inotives_tradings.users_history (changed_at);
CREATE INDEX ON inotives_tradings.users_history (changed_by);

-- 4. Attach Triggers
-- A. Auditing (Timestamps)
CREATE TRIGGER auditing_trigger_users
    BEFORE INSERT OR UPDATE ON inotives_tradings.users
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.set_audit_fields();

-- B. Soft Delete (Intercept physical DELETE)
CREATE TRIGGER soft_delete_trigger_users
    BEFORE DELETE ON inotives_tradings.users
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.trigger_soft_delete();

-- C. Versioning (Archive old rows to history)
-- Note: versioning function expects: history_table_name
CREATE TRIGGER versioning_trigger_users
    BEFORE UPDATE OR DELETE ON inotives_tradings.users
    FOR EACH ROW EXECUTE PROCEDURE inotives_tradings.versioning('inotives_tradings.users_history');

-- 5. SEED initial system user
ALTER TABLE inotives_tradings.users DISABLE TRIGGER ALL;

INSERT INTO inotives_tradings.users (id, username, display_name, role, created_by, updated_by)
VALUES (1, 'system_admin', 'System Admin', 'admin' , 1, 1);


-- Reset sequence to 2
SELECT setval(pg_get_serial_sequence('inotives_tradings.users', 'id'), coalesce(max(id), 1), true) FROM inotives_tradings.users;

ALTER TABLE inotives_tradings.users ENABLE TRIGGER ALL;


-- migrate:down
DROP TABLE IF EXISTS inotives_tradings.users_history;
DROP TABLE IF EXISTS inotives_tradings.users CASCADE;
DROP TYPE IF EXISTS inotives_tradings.user_status;
