-- migrate:up

-- 1. Create the ENUM type

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_status') THEN
        CREATE TYPE base.user_status AS ENUM ('active', 'suspend', 'deleted');
    END IF;
END $$;


-- 2. Create base table
CREATE TABLE base.users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',  -- will create a seperate table for roles and permissions in future, for now keep in simple. 
    status base.user_status NOT NULL DEFAULT 'active',
    
    -- Audit fields
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    
    -- Soft Delete fields
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Temporal field
    sys_period TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership/Audit references
    created_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_users CHECK (
        (deleted_at is null and deleted_by is null)
        or (deleted_at is not null and deleted_by is not null)
    )
);

-- 3. Create history table
CREATE TABLE base.users_history (LIKE base.users INCLUDING DEFAULTS);
CREATE INDEX ON base.users_history (sys_period);

-- 4. Attach Triggers
-- A. Auditing (Timestamps)
CREATE TRIGGER auditing_trigger_users
    BEFORE INSERT OR UPDATE ON base.users
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

-- B. Soft Delete (Intercept physical DELETE)
CREATE TRIGGER soft_delete_trigger_users
    BEFORE DELETE ON base.users
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

-- C. Versioning (Archive old rows to history)
-- Note: versioning function expects: history_table_name
CREATE TRIGGER versioning_trigger_users
    BEFORE INSERT OR UPDATE OR DELETE ON base.users
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.users_history');

-- 5. SEED initial system user
ALTER TABLE base.users DISABLE TRIGGER ALL;

INSERT INTO base.users (id, username, display_name, role, created_by, updated_by)
VALUES (1, 'system_admin', 'System Admin', 'admin' , 1, 1);


-- Reset sequence to 2
SELECT setval(pg_get_serial_sequence('base.users', 'id'), coalesce(max(id), 1), true) FROM base.users;

ALTER TABLE base.users ENABLE TRIGGER ALL;


-- migrate:down
DROP TABLE IF EXISTS base.users_history;
DROP TABLE IF EXISTS base.users;
DROP TYPE IF EXISTS base.user_status;
