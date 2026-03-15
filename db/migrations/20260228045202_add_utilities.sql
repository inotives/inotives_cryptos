-- migrate:up

-- 0. EXTENSIONS (Required for advanced utilities)
CREATE EXTENSION IF NOT EXISTS "unaccent";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. SCHEMA SETUP
CREATE SCHEMA IF NOT EXISTS inotives_tradings;

-- 2. AUDITING: Automatic Timestamps
-- Updates created_at/updated_at columns automatically
CREATE OR REPLACE FUNCTION inotives_tradings.set_audit_fields()
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
CREATE OR REPLACE FUNCTION inotives_tradings.trigger_soft_delete()
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
-- Manually call this: SELECT inotives_tradings.undelete_record('inotives_tradings', 'users', 1);
CREATE OR REPLACE FUNCTION inotives_tradings.undelete_record(
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
-- On UPDATE: bumps version, diffs OLD vs NEW, archives old row with change metadata.
-- On DELETE: archives old row with change_type='DELETE'.
-- Requires tables to have: version INTEGER, sys_period TSTZRANGE.
-- History tables require extra columns: changed_at, changed_by, change_type, changes.
-- App context for actor: SET LOCAL app.current_user_id = '<id>';
CREATE OR REPLACE FUNCTION inotives_tradings.versioning()
RETURNS TRIGGER AS $$
DECLARE
  history_table  text        := TG_ARGV[0];
  now_ts         timestamptz := CURRENT_TIMESTAMP;
  period_start   timestamptz;
  changed_by_val bigint;
  changes_json   jsonb;
  old_json       jsonb;
  new_json       jsonb;
  key            text;
BEGIN
  IF (TG_OP = 'UPDATE' OR TG_OP = 'DELETE') THEN
    period_start   := COALESCE(lower(OLD.sys_period), now_ts);
    changed_by_val := (NULLIF(current_setting('app.current_user_id', true), ''))::bigint;

    -- Compute field-level diff: { "field": { "old": <val>, "new": <val> } }
    -- Diff is captured before version/sys_period are bumped so it reflects only user-driven changes.
    IF (TG_OP = 'UPDATE') THEN
      old_json     := to_jsonb(OLD);
      new_json     := to_jsonb(NEW);
      changes_json := '{}';
      FOR key IN SELECT jsonb_object_keys(old_json) LOOP
        IF old_json->key IS DISTINCT FROM new_json->key THEN
          changes_json := changes_json || jsonb_build_object(
            key, jsonb_build_object('old', old_json->key, 'new', new_json->key)
          );
        END IF;
      END LOOP;
      -- Skip archive if nothing actually changed
      IF changes_json = '{}' THEN
        RETURN NEW;
      END IF;
    END IF;

    -- Archive old row: close sys_period and attach change metadata
    EXECUTE format(
      'INSERT INTO %s
       SELECT * FROM jsonb_populate_record(
           NULL::%s,
           to_jsonb($1) || jsonb_build_object(
               ''sys_period'',  tstzrange($2, $3),
               ''changed_at'',  $3,
               ''changed_by'',  $4,
               ''change_type'', $5,
               ''changes'',     $6
           )
       )',
      history_table, history_table
    ) USING OLD, period_start, now_ts, changed_by_val, TG_OP, changes_json;
  END IF;

  IF (TG_OP = 'UPDATE') THEN
    -- Bump version and open a fresh period on the live row
    NEW.version    := OLD.version + 1;
    NEW.sys_period := tstzrange(now_ts, NULL);
    RETURN NEW;
  ELSIF (TG_OP = 'DELETE') THEN
    RETURN OLD;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- 6. UTILITIES: Slugify (For clean URLs/IDs)
CREATE OR REPLACE FUNCTION inotives_tradings.slugify(value TEXT)
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
CREATE OR REPLACE FUNCTION inotives_tradings.random_string(length INTEGER)
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
DROP FUNCTION IF EXISTS inotives_tradings.random_string(INTEGER);
DROP FUNCTION IF EXISTS inotives_tradings.slugify(TEXT);
DROP FUNCTION IF EXISTS inotives_tradings.versioning();
DROP FUNCTION IF EXISTS inotives_tradings.undelete_record(text, text, bigint);
DROP FUNCTION IF EXISTS inotives_tradings.trigger_soft_delete();
DROP FUNCTION IF EXISTS inotives_tradings.set_audit_fields();
DROP SCHEMA IF EXISTS inotives_tradings CASCADE;