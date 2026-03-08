# CLAUDE.md — Inotives Cryptos Project Guidelines

## Project Architecture & Stack

This is a containerised, data-centric crypto project. All development must adhere to the following stack:

- **Package Manager**: `uv` (fast Python package installer and resolver)
- **Python Stack**: `requests` (API calls), `asyncpg` (async DB), `ccxt` (exchange API), `pydantic-settings` (config)
- **Database**: PostgreSQL/TimescaleDB (storage), `dbmate` (migrations), `dbt` (transformations)
- **Orchestration**: Prefect 3 (scheduled data pipelines)
- **DevOps**: Docker + Docker Compose, Makefile (task automation)

---

## Folder Structure

```
inotives_cryptos/
├── analytics/              # dbt project (staging + mart models)
├── apps/
│   ├── bots/               # Polling bots (NOT Prefect — simple asyncio loops)
│   │   ├── common/         # Shared: config.py, db.py, exchange.py
│   │   ├── pricing_bot/    # Polls exchange tickers → base.price_observations
│   │   └── trader_bot/     # Monitors cycles, executes DCA-Grid orders
│   └── pipelines/          # Prefect flows for scheduled batch ingestion
│       └── src/
│           ├── config.py
│           ├── main.py     # Registers all deployments (run once)
│           └── flows/      # One file per data source
├── configs/envs/           # .env.local, .env.dev, .env.prod (never commit)
├── db/
│   ├── init/               # Shell scripts run on first Postgres container start
│   └── migrations/         # dbmate SQL migration files
├── docker-compose.yml
├── Makefile
└── pyproject.toml          # uv workspace root
```

**Key rules:**
- `apps/bots/` — asyncio polling scripts only. No Prefect.
- `apps/pipelines/` — all Prefect-orchestrated flows live here. Also orchestrates dbt runs.
- `analytics/` — dbt project, standalone. Never merge into pipelines.
- Never hardcode credentials. Always load from `configs/envs/.env.[local|dev|prod]`.

---

## Python & Virtual Environment

- Always use `uv` for dependency management. Never use `pip install` directly.
- Add packages: `uv add <package>` inside the relevant app folder.
- Run scripts: `uv run <script>` to ensure correct environment.
- Each app (`apps/bots`, `apps/pipelines`, `analytics`) is a uv workspace member.

---

## Makefile Commands

Always check the Makefile before running manual commands.

```bash
make services-up                    # Start Docker services (DB + Prefect + worker)
make services-down                  # Stop Docker services

make migrate-up                     # Apply all pending dbmate migrations
make migrate-down                   # Roll back last migration
make migrate-status                 # Show migration state
make migrate-new name=<name>        # Create a new migration file

make prefect-deploy                 # Register Prefect flow deployments (run once)
make prefect-ui                     # Open http://localhost:4200

make init                           # Bootstrap venv on fresh clone
```

---

## Database Conventions

### Schema
All tables live in the `base` schema. The `public` schema is not used for app tables.

### Standard Table Pattern
Every mutable table must follow this pattern:

```sql
CREATE TABLE base.<table_name> (
    id          BIGSERIAL PRIMARY KEY,
    -- ... business columns ...
    metadata    JSONB DEFAULT '{}' NOT NULL,

    -- Audit
    created_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    -- Soft delete
    deleted_at  TIMESTAMPTZ,
    deleted_by  BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    -- Versioning
    version     INTEGER NOT NULL DEFAULT 1,
    sys_period  TSTZRANGE NOT NULL DEFAULT TSTZRANGE(current_timestamp, null),

    -- Ownership
    created_by  BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,
    updated_by  BIGINT REFERENCES base.users(id) DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT chk_deleted_fields_<table> CHECK (
        (deleted_at IS NULL AND deleted_by IS NULL) OR
        (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
    )
);
```

### History Table Pattern
Every mutable table needs a corresponding history table:

```sql
CREATE TABLE base.<table>_history (LIKE base.<table> INCLUDING DEFAULTS);
ALTER TABLE base.<table>_history
    ADD COLUMN changed_at  TIMESTAMPTZ,
    ADD COLUMN changed_by  BIGINT,
    ADD COLUMN change_type TEXT,
    ADD COLUMN changes     JSONB;
CREATE INDEX ON base.<table>_history (sys_period);
CREATE INDEX ON base.<table>_history (changed_at);
```

### Trigger Pattern (always 3 triggers per table)

```sql
-- 1. Audit (BEFORE INSERT OR UPDATE)
CREATE TRIGGER auditing_trigger_<table>
    BEFORE INSERT OR UPDATE ON base.<table>
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();

-- 2. Soft delete (BEFORE DELETE)
CREATE TRIGGER soft_delete_trigger_<table>
    BEFORE DELETE ON base.<table>
    FOR EACH ROW EXECUTE PROCEDURE base.trigger_soft_delete();

-- 3. Versioning (BEFORE UPDATE OR DELETE — NOT INSERT)
CREATE TRIGGER versioning_trigger_<table>
    BEFORE UPDATE OR DELETE ON base.<table>
    FOR EACH ROW EXECUTE PROCEDURE base.versioning('base.<table>_history');
```

### Migration Rules
- One logical change per migration file (atomic).
- Filename format: `YYYYMMDDHHMMSS_<description>.sql`
- Always include `-- migrate:up` and `-- migrate:down` blocks.
- Down migration must use `DROP TABLE IF EXISTS ... CASCADE` for tables with FK references.
- ENUM types must be schema-qualified: `base.<type_name>`, not just `<type_name>`.
- Use `NUMERIC(36,18)` for crypto prices, `NUMERIC(36,2)` for USD aggregates.
- For nullable unique columns, use partial unique indexes instead of UNIQUE constraints:
  ```sql
  CREATE UNIQUE INDEX uq_<name>_present ON base.<table> (col_a, col_b) WHERE nullable_col IS NOT NULL;
  CREATE UNIQUE INDEX uq_<name>_absent  ON base.<table> (col_a, col_b) WHERE nullable_col IS NULL;
  ```

### Append-only Tables (metrics, events, executions)
Time-series and log tables do NOT need soft delete, versioning, or history tables:
- `base.asset_metrics_1d`, `base.asset_metrics_intraday`
- `base.price_observations`
- `base.trade_executions`, `base.trade_pnl`
- `base.system_events`, `base.portfolio_snapshots`

---

## Prefect (apps/pipelines)

- Prefect server runs in Docker, UI at `http://localhost:4200`.
- Prefect internal metadata is stored in the `prefect_internal` Postgres database (separate from app DB).
- Worker type: `process` (pool name: `data-eng-pool`).
- Flow schedules are managed by Prefect — no custom schedules table needed.
- Register/update deployments: `make prefect-deploy` (runs `src/main.py`).
- Each flow file corresponds to one data source (e.g. `flows/coingecko.py`).
- Prefect also orchestrates dbt runs — `analytics/` stays standalone, pipelines calls it.

---

## Trading Bots (apps/bots)

- Run as long-lived asyncio polling loops (not Prefect).
- `pricing_bot` polls ccxt tickers every 60s → inserts to `base.price_observations`.
- `trader_bot` monitors active `trade_strategies` → executes DCA-Grid logic.
- Strategy parameters, cycle state, and order context live in `metadata JSONB` — never add strategy-specific columns to the schema.
- Capital allocation tracked via `base.capital_locks` + `base.venue_available_capital` view.

---

## Git Workflow

- **Personal GitHub account**: `inotives` — `inotives@gmail.com`
- **SSH host alias**: `github-personal` (maps to `~/.ssh/id_ed25519_inotives` with `IdentitiesOnly yes`)
- **Branch naming**: `feat-<description>` for features, `INO-XXXX/<description>` for tracked issues
- **Commit scope**: keep commits focused — don't mix migrations with app code in the same commit
- **Always set local git identity** for this repo (already configured):
  ```bash
  git config --local user.email "inotives@gmail.com"
  git config --local user.name "inotives"
  ```

---

## Configuration & Secrets

- Never hardcode credentials.
- Load env vars strictly from `configs/envs/.env.[local|dev|prod]`.
- `.env.*` files are gitignored — only `.env.example` is committed.
- Pydantic `Settings` class is the standard config pattern across all apps.
