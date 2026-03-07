-- migrate:up

-- 0. EXTENSIONS (Required for advanced utilities)
CREATE EXTENSION IF NOT EXISTS "unaccent";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. SCHEMA SETUP
CREATE SCHEMA IF NOT EXISTS base;

-- 2. AUDITING: Automatic Timestamps
-- Updates created_at/updated_at columns automatically
CREATE OR REPLACE FUNCTION base.set_audit_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        NEW.created_at = CURRENT_TIMESTAMP;
        NEW.updated_at = CURRENT_TIMESTAMP;
    ELSIF (TG_OP = 'UPDATE') THEN
        NEW.updated_at = CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. SOFT DELETE: The "Recycle Bin" Engine
-- Intercepts DELETE commands and converts them to UPDATE
CREATE OR REPLACE FUNCTION base.trigger_soft_delete()
RETURNS TRIGGER AS $$
BEGIN
    -- Avoid infinite loops if delete is called on an already soft-deleted row
    IF (OLD.deleted_at IS NOT NULL) THEN
        RETURN OLD;
    END IF;

    -- Update the row. Captures WHO and WHEN.
    -- App context: SET app.current_user_id = '1';
    EXECUTE format(
        'UPDATE %I.%I SET 
            deleted_at = CURRENT_TIMESTAMP, 
            deleted_by = (NULLIF(current_setting(''app.current_user_id'', true), ''''))::bigint 
         WHERE id = $1.id', 
        TG_TABLE_SCHEMA, TG_TABLE_NAME
    ) USING OLD;

    RETURN NULL; -- Cancels the physical deletion
END;
$$ LANGUAGE plpgsql;

-- 4. UNDELETE: The "Resurrection" Utility
-- Manually call this: SELECT base.undelete_record('base', 'users', 1);
CREATE OR REPLACE FUNCTION base.undelete_record(
    target_schema text, 
    target_table text, 
    record_id bigint
)
RETURNS void AS $$
BEGIN
    EXECUTE format(
        'UPDATE %I.%I SET 
            deleted_at = NULL, 
            deleted_by = NULL,
            updated_at = CURRENT_TIMESTAMP 
         WHERE id = $1 AND deleted_at IS NOT NULL', 
        target_schema, target_table
    ) USING record_id;
END;
$$ LANGUAGE plpgsql;

-- 5. VERSIONING: The "Time Machine" Engine
-- Archives the "Before" state of a row into a history table
CREATE OR REPLACE FUNCTION base.versioning()
RETURNS TRIGGER AS $$
DECLARE
  history_table text := TG_ARGV[0];
BEGIN
  -- We ALWAYS archive the OLD state before any change
  IF (TG_OP = 'UPDATE' OR TG_OP = 'DELETE') THEN
    EXECUTE format('INSERT INTO %s SELECT $1.*', history_table) USING OLD;
  END IF;
  
  -- Logic for the trigger chain:
  IF (TG_OP = 'DELETE') THEN
    RETURN OLD; -- Allows physical deletion to continue
  ELSE
    RETURN NEW; -- Allows inserts/updates to continue
  END IF;
END;
$$ LANGUAGE plpgsql;

-- 6. UTILITIES: Slugify (For clean URLs/IDs)
CREATE OR REPLACE FUNCTION base.slugify(value TEXT)
RETURNS TEXT AS $$
BEGIN
  RETURN regexp_replace(
    regexp_replace(
      lower(unaccent(value)), 
      '[^a-z0-9\\-_]+', '-', 'gi'
    ),
    '(^-+|-+$)', '', 'g'
  );
END;
$$ LANGUAGE plpgsql STRICT IMMUTABLE;

-- 7. UTILITIES: Generate Random String
CREATE OR REPLACE FUNCTION base.random_string(length INTEGER)
RETURNS TEXT AS $$
DECLARE
  chars TEXT := 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  result TEXT := '';
  i INTEGER := 0;
BEGIN
  FOR i IN 1..length LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::integer, 1);
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- migrate:down
DROP FUNCTION IF EXISTS base.random_string(INTEGER);
DROP FUNCTION IF EXISTS base.slugify(TEXT);
DROP FUNCTION IF EXISTS base.versioning();
DROP FUNCTION IF EXISTS base.undelete_record(text, text, bigint);
DROP FUNCTION IF EXISTS base.trigger_soft_delete();
DROP FUNCTION IF EXISTS base.set_audit_fields();
DROP SCHEMA IF EXISTS base CASCADE;