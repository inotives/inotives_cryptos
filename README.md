# Inotives Cryptos

A personal project for building an automated crypto trading system — from raw data ingestion to live DCA-grid trading. The stack is fully containerised and data-centric, built around PostgreSQL, Python, dbt, and Prefect.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Data Sources                         │
│         CoinGecko · CoinMarketCap · Binance (ccxt)          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  apps/pipelines  (Prefect)                   │
│   Scheduled flows: fetch reference data, OHLCV, prices      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               PostgreSQL  (TimescaleDB)                      │
│   base schema: networks, assets, venues, trading tables,    │
│   price observations, metrics, portfolio snapshots          │
└──────────┬──────────────────────────────────┬───────────────┘
           │                                  │
           ▼                                  ▼
┌─────────────────────┐           ┌───────────────────────────┐
│  analytics  (dbt)   │           │  apps/bots  (Python)      │
│  Staging + marts    │           │  pricing_bot · trader_bot │
│  models & metrics   │           │  DCA-Grid strategy        │
└─────────────────────┘           └───────────────────────────┘
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Package manager | `uv` |
| Database | PostgreSQL 16 (TimescaleDB) |
| Migrations | `dbmate` |
| Transformations | `dbt` |
| Orchestration | Prefect 3 |
| Exchange API | `ccxt` (Binance) |
| Market data | CoinGecko, CoinMarketCap |
| Containerisation | Docker + Docker Compose |
| Task automation | Makefile |

---

## Project Structure

```
inotives_cryptos/
├── analytics/              # dbt project (staging + mart models)
├── apps/
│   ├── bots/               # Pricing bot + trader bot (asyncio polling)
│   │   ├── common/         # Shared DB pool, exchange client, config
│   │   ├── pricing_bot/    # Polls exchange tickers → price_observations
│   │   └── trader_bot/     # Monitors cycles, places DCA-grid orders
│   └── pipelines/          # Prefect flows for scheduled data ingestion
│       └── src/flows/      # CoinGecko, CoinMarketCap, exchange flows
├── configs/envs/           # Environment files (.env.local, .env.prod)
├── db/
│   ├── init/               # DB init scripts (runs on first container start)
│   └── migrations/         # dbmate SQL migration files
├── docker-compose.yml
├── Makefile
└── pyproject.toml          # uv workspace root
```

---

## Getting Started

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 1. Clone the repo

```bash
git clone git@github.com:inotives/inotives_cryptos.git
cd inotives_cryptos
```

### 2. Set up environment

Copy the example env file and fill in your credentials:

```bash
cp configs/envs/.env.example configs/envs/.env.local
```

Key values to update in `.env.local`:

```env
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=inotives_db

COINGECKO_API_KEY=your_key
COINMARKETCAP_API_KEY=your_key

# Binance (for bots)
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
```

### 3. Install Python dependencies

```bash
make init
```

### 4. Start services

```bash
make services-up
```

This starts:
- **PostgreSQL** (TimescaleDB) on port `5435`
- **Prefect server** (UI) on port `4200`
- **Fetcher worker** (Prefect process worker)

### 5. Run database migrations

```bash
make migrate-up
```

This applies all 15 migrations in order, creating the full `base` schema.

### 6. Register Prefect flow deployments

```bash
make prefect-deploy
```

Registers scheduled flows with the Prefect server (e.g. daily CoinGecko sync at 01:00 UTC). Only needs to be run once, or after adding new flows.

### 7. Open Prefect UI

```bash
make prefect-ui
# → http://localhost:4200
```

---

## Makefile Commands

```bash
make services-up       # Start all Docker services
make services-down     # Stop all Docker services

make migrate-up        # Apply all pending migrations
make migrate-down      # Roll back the last migration
make migrate-status    # Show migration status
make migrate-new name=<migration_name>  # Create a new migration file

make prefect-deploy    # Register flow deployments with Prefect server
make prefect-ui        # Open Prefect UI in browser
```

---

## Database Schema

All tables live in the `base` schema. Every mutable table follows a consistent pattern:

- **Audit fields** — `created_at`, `updated_at`, `created_by`, `updated_by`
- **Soft delete** — `deleted_at`, `deleted_by` (DELETE is intercepted by trigger)
- **Versioning** — `version` counter + `sys_period` temporal range
- **History table** — `<table>_history` captures field-level diffs on every update/delete

### Migrations

| # | File | Description |
|---|---|---|
| 1 | `add_utilities` | `base` schema, audit/soft-delete/versioning trigger functions |
| 2 | `create_users` | System users + seed admin |
| 3 | `create_networks` | Blockchain networks (`BTC`, `ETH`, `SOL`, ...) |
| 4 | `create_assets` | Crypto assets with type and origin network |
| 5 | `create_network_assets` | Asset deployments per network (contract addresses) |
| 6 | `create_data_sources` | External data source registry (CoinGecko, CMC, Binance) |
| 7 | `create_asset_metrics_1d` | Daily OHLCV + market cap + supply metrics |
| 8 | `create_source_mappings` | Asset and network identifier mappings per source |
| 9 | `create_venue_tables` | Venues (exchange accounts, wallets), balances, transfers |
| 10 | `create_trading_tables` | Strategies, cycles, orders, executions, PnL |
| 11 | `create_price_observations` | Live price snapshots (1–5 min intervals) |
| 12 | `create_capital_locks` | Capital reservation per active cycle + available balance view |
| 13 | `create_asset_metrics_intraday` | Intraday OHLCV (1m, 5m, 15m, 30m, 1h, 4h) |
| 14 | `create_system_events` | Bot operational event log |
| 15 | `create_portfolio_snapshots` | Daily portfolio valuation snapshots |

---

## Progress

### Done
- [x] Full database schema (15 migrations) covering reference data, trading, metrics, and observability
- [x] DB utility functions: audit fields, soft delete, field-level versioning with history tables
- [x] Docker Compose setup with TimescaleDB + Prefect server + fetcher worker
- [x] Prefect pipeline scaffold with CoinGecko asset platforms flow

### In Progress
- [ ] **apps/bots** — pricing bot (live price polling) and trader bot (DCA-Grid strategy execution)
- [ ] **apps/pipelines** — additional Prefect flows: CoinMarketCap, exchange OHLCV, daily metrics
- [ ] **analytics** — dbt staging and mart models for `asset_metrics_1d`, portfolio performance

### Planned
- [ ] Prefect + dbt integration (run dbt models as part of daily ingestion pipeline)
- [ ] Portfolio snapshot automation (end-of-day dbt model → `portfolio_snapshots`)
- [ ] Strategy performance views (dbt)
- [ ] Additional strategies beyond DCA-Grid

---

## Environment Files

| File | Purpose |
|---|---|
| `.env.local` | Local development (default) |
| `.env.dev` | Shared dev/staging environment |
| `.env.prod` | Production |

All `.env.*` files are gitignored. Never commit credentials.
