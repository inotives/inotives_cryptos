#!/bin/bash
# Creates additional databases on first Postgres container start.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE prefect_internal'
    WHERE NOT EXISTS (
        SELECT FROM pg_database WHERE datname = 'prefect_internal'
    )\gexec
EOSQL
