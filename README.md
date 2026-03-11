# Inotives Cryptos

A personal project for building an automated crypto trading system — from raw data ingestion to live volatility-adaptive grid trading. The stack is fully containerised and data-centric, built around PostgreSQL/TimescaleDB, Python, dbt, and Prefect 3.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          Data Sources                           │
│        CoinGecko · CoinMarketCap · Crypto.com (ccxt)            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 apps/pipelines  (Prefect 3)                      │
│  Mon 00:00  coingecko-platforms-weekly → coingecko.raw_platforms │
│  Mon 00:30  coingecko-coins-list-weekly → coingecko.raw_coins   │
│  02:00 UTC  daily-data-pipeline:                                 │
│               CoinGecko OHLCV          → base.asset_metrics_1d  │
│               compute indicators       → base.asset_indicators_1d│
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                PostgreSQL (TimescaleDB)                          │
│  coingecko schema: raw_coins · raw_platforms  (CG universe)     │
│  base schema:  networks · assets  (trading allow-list)          │
│                data_sources · asset_source_mappings             │
│                asset_metrics_1d · asset_indicators_1d           │
│                price_observations · trading tables              │
│                venue · capital_locks · portfolio_snapshots       │
└──────────┬──────────────────────────────────────┬───────────────┘
           │                                      │
           ▼                                      ▼
┌─────────────────────┐           ┌───────────────────────────────┐
│  analytics  (dbt)   │           │  apps/bots  (Python asyncio)  │
│  Staging + marts    │           │  pricing_bot (ticker polling) │
│  models & metrics   │           │  trader_bot  (DCA grid)       │
└─────────────────────┘           │  backtest    (simulation)     │
                                  └───────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│               Grafana  (port 3030)                              │
│  Market Overview · Technical Indicators · Trading Performance   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Package manager | `uv` (workspace) |
| Database | PostgreSQL 16 (TimescaleDB) |
| Migrations | `dbmate` |
| Transformations | `dbt` |
| Orchestration | Prefect 3 |
| Exchange API | `ccxt` (Crypto.com, Binance, ...) |
| Market data | CoinGecko · CoinMarketCap |
| Indicator maths | `pandas-ta` |
| Dashboards / BI | Grafana |
| Containerisation | Docker + Docker Compose |
| Task automation | `Makefile` |

---

## Project Structure

```
inotives_cryptos/
├── analytics/                  # dbt project (staging + mart models)
├── apps/
│   ├── bots/                   # Trading bots (asyncio polling loops)
│   │   ├── common/             # DB pool, exchange connections, config
│   │   │   └── connections/    # ccxt REST wrapper + per-exchange subclasses
│   │   │       └── paper.py    # PaperTradingConnection (simulated fills)
│   │   ├── backtest/           # DCA grid backtesting engine
│   │   │   ├── engine.py       # Pure-Python simulation engine
│   │   │   ├── runner.py       # DB loader + CLI entry point
│   │   │   └── models.py       # BacktestCandle dataclass
│   │   ├── pricing_bot/        # Polls tickers → base.price_observations
│   │   ├── trader_bot/         # Executes volatility-adaptive grid strategy
│   │   │   ├── main.py         # Bot entry point (polling loop + fee sync)
│   │   │   └── strategies/
│   │   │       └── dca_grid.py # DcaGridStrategy (full implementation)
│   │   └── tests/              # pytest suite for backtest + strategy
│   └── pipelines/              # Prefect 3 scheduled data pipelines
│       ├── src/
│       │   ├── api/            # CoinGecko + CoinMarketCap API clients
│       │   ├── flows/          # coingecko_platforms, coingecko_coins_list,
│       │   │                   # coingecko_ohlcv_1d, compute_indicators_1d,
│       │   │                   # daily_pipeline
│       │   ├── config.py       # Settings (loaded from .env.local)
│       │   └── main.py         # Flow import index
│       └── prefect.yaml        # Deployment schedules (read by prefect deploy)
├── configs/envs/               # Environment files (.env.example, .env.local)
├── db/
│   ├── init/                   # Postgres init scripts (first container start only)
│   ├── migrations/             # dbmate SQL migration files (26 total)
│   └── seeds/                  # CSV seed data
├── grafana/
│   ├── provisioning/           # Auto-provisioned datasource + dashboard provider
│   └── dashboards/             # market_overview, technical_indicators, trading_performance
├── scripts/                    # One-off scripts (seeding, setup, management)
│   ├── seed_data_sources_from_csv.py  # Still needed — data sources aren't from CoinGecko
│   ├── seed_metrics_1d_from_csv.py    # Historical OHLCV backfill
│   ├── allowlist_asset.py      # Promote CoinGecko coin → base.assets (trading allow-list)
│   ├── allowlist_network.py    # Promote CoinGecko platform → base.networks (allow-list)
│   ├── setup_paper_trading.py  # Create venue + strategy for paper trading
│   ├── manage_trading.py       # Interactive CLI for strategy/cycle CRUD
│   └── run_backtest_sweep.py   # Sweep multiple grid configs across date windows
├── docker-compose.yml          # DB + Prefect server + fetcher worker + Grafana
├── Makefile                    # All common dev commands
└── pyproject.toml              # uv workspace root
```

---

## Getting Started (from scratch)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — running
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

### Step 1 — Clone the repository

```bash
git clone git@github.com:inotives/inotives_cryptos.git
cd inotives_cryptos
```

---

### Step 2 — Configure environment

```bash
cp configs/envs/.env.example configs/envs/.env.local
```

Edit `.env.local` and fill in your values:

```env
# Database
DB_HOST=localhost
DB_PORT=5435
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=inotives_db

# Exchange API keys (optional — public endpoints work without keys)
CRYPTOCOM_API_KEY=your_key
CRYPTOCOM_API_SECRET=your_secret

# Market data API keys
COINGECKO_API_KEY=your_key          # optional — free tier works without a key
COINGECKO_API_KEY_TYPE=demo         # "demo" (free) or "pro" (paid)
COINMARKETCAP_API_KEY=your_key
```

---

### Step 3 — Install Python dependencies

```bash
make init
```

This creates a virtual environment and installs all workspace dependencies via `uv`.

---

### Step 4 — Start Docker services

```bash
make services-up
```

Starts four containers:

| Container | Purpose | Port |
|---|---|---|
| `inotives_db` | TimescaleDB (PostgreSQL 16) | `5435` |
| `inotives_prefect` | Prefect 3 orchestration server | `4200` |
| `inotives_fetcher_worker` | Prefect process worker (`data-eng-pool`) | — |
| `inotives_grafana` | Grafana dashboards | `3030` |

> **Note:** On the very first run, the `prefect_internal` database is created automatically by `db/init/01_create_databases.sh`. If you restart the stack after the volume already exists, you may need to create it manually:
> ```bash
> docker exec inotives_db psql -U postgres -c "CREATE DATABASE prefect_internal;"
> ```

---

### Step 5 — Run database migrations

```bash
make migrate-up
```

Applies all 26 migrations in order, building the complete `base` and `coingecko` schemas.

---

### Step 6 — Bootstrap reference data

Run the bootstrap target to seed data sources, sync the CoinGecko reference tables, and allow-list the default networks and assets (ETH, BTC, SOL):

```bash
make bootstrap
```

This runs five steps in sequence:
1. Seeds `base.data_sources` (CoinGecko, CoinMarketCap, exchanges)
2. Syncs `coingecko.raw_platforms` from CoinGecko `/asset_platforms`
3. Syncs `coingecko.raw_coins` from CoinGecko `/coins/list`
4. Allow-lists Ethereum, Bitcoin, Solana networks → `base.networks`
5. Allow-lists BTC, ETH, SOL assets → `base.assets` + `base.asset_source_mappings`

> **Note:** ETH/BTC/SOL are the recommended defaults. To add more assets or networks after bootstrap:
> ```bash
> make allowlist-network coingecko_id=binance-smart-chain
> make allowlist-asset   coingecko_id=dogecoin cmc_id=74
> ```

---

### Step 7 — Seed historical OHLCV data

Seed CoinMarketCap CSV exports into `asset_metrics_1d`. The asset code and source are inferred from the filename automatically:

```bash
# BTC — full history (2010–2026)
make seed-metrics-1d csv=db/seeds/btc_historical_data_20100513-20260309.csv
```

Then backfill all indicators in one shot:

```bash
uv run --env-file configs/envs/.env.local --project apps/pipelines python - <<'EOF'
import asyncio, sys
sys.path.insert(0, "apps/pipelines")
from src.flows.compute_indicators_1d import compute_indicators_backfill_flow
asyncio.run(compute_indicators_backfill_flow(asset_codes=["btc", "sol"]))
EOF
```

---

### Step 8 — Register Prefect deployments

```bash
make prefect-deploy
```

Registers scheduled deployments (reads `apps/pipelines/prefect.yaml`):

| Deployment | Schedule | Description |
|---|---|---|
| `coingecko-platforms-weekly` | Mon 00:00 UTC | Sync CoinGecko platforms → `coingecko.raw_platforms` |
| `coingecko-coins-list-weekly` | Mon 00:30 UTC | Sync CoinGecko coin list → `coingecko.raw_coins` |
| `daily-data-pipeline` | 02:00 UTC daily | Fetch OHLCV → compute indicators |

Only needs to be run once, or after adding/changing flows.

---

### Step 9 — Set up paper trading

Create the paper trading venue and strategies in the DB:

```bash
make setup-paper-trading
```

This creates:
- A `Crypto.com (Paper)` venue row
- A `BTC/USDT DCA Grid — Paper` strategy with `status=ACTIVE`
- A `SOL/USDT DCA Grid — Paper` strategy with `status=ACTIVE`

Safe to re-run — checks before inserting.

---

### Step 10 — Run the bots

Start both bots in separate terminals:

```bash
# Terminal 1 — pricing bot (polls live tickers every 60s)
make pricing-bot

# Terminal 2 — trader bot (executes grid strategy)
make trader-bot
```

The trader bot polls `base.trade_strategies` for active strategies and runs the DCA grid loop every 60 seconds. At startup it syncs live maker/taker fees from the exchange and updates any changed values in the DB. Paper trading uses `PaperTradingConnection` which simulates fills instantly without placing real orders.

---

### Step 11 — Open Grafana

```bash
make grafana-ui
# → http://localhost:3030
```

Default login: `admin` / `admin` (change via `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` in `.env.local`).

The TimescaleDB datasource and all dashboards are provisioned automatically — no manual setup needed.

---

### Step 12 — Open Prefect UI

```bash
make prefect-ui
# → http://localhost:4200
```

From the UI you can monitor flow runs, view schedules, and trigger manual runs.

---

## Makefile Reference

### Services

```bash
make services-up                   # Start all Docker containers
make services-down                 # Stop all Docker containers
```

### Database

```bash
make migrate-up                    # Apply all pending migrations
make migrate-down                  # Roll back the last migration
make migrate-status                # Show migration status
make migrate-new name=<name>       # Create a new migration file
```

### Bootstrap & Reference Data

```bash
make bootstrap                     # Full initial setup: seed + sync CoinGecko + allow-list defaults
make sync-coingecko-platforms      # Manually run platform sync (bypasses Prefect scheduler)
make sync-coingecko-coins          # Manually run coin list sync (bypasses Prefect scheduler)
```

### Allow-listing Assets & Networks

```bash
# Preview before writing
make allowlist-asset-dry   coingecko_id=bitcoin
make allowlist-network-dry coingecko_id=ethereum

# Promote to trading allow-list
make allowlist-asset   coingecko_id=bitcoin
make allowlist-asset   coingecko_id=ethereum cmc_id=1027
make allowlist-network coingecko_id=ethereum
make allowlist-network coingecko_id=binance-smart-chain code=BSC
```

### Seeding

```bash
make seed-data-sources             # Seed base.data_sources from CSV (required once)
make seed-metrics-1d csv=<path>    # Import daily OHLCV from a CMC CSV export
make seed-metrics-1d-dry csv=<path>
```

### Bots

```bash
make pricing-bot                   # Start the pricing bot (polls tickers every 60s)
make trader-bot                    # Start the trader bot (runs active grid strategies)
make setup-paper-trading           # Create paper trading venue + strategies in DB
make manage-trading                # Interactive CLI for strategy/cycle management
```

### Grafana

```bash
make grafana-ui                    # Open Grafana at http://localhost:3030
```

### Prefect

```bash
make prefect-deploy                # Register flow deployments (reads prefect.yaml)
make prefect-ui                    # Open Prefect UI at http://localhost:4200
```

---

## Database Schema

### Schema layout

| Schema | Purpose |
|---|---|
| `coingecko` | Raw data landed directly from CoinGecko API — full universe, no curation |
| `base` | Internal curated data — trading allow-list, metrics, indicators, trading state |

### Allow-list model

`coingecko.raw_coins` and `coingecko.raw_platforms` hold the full CoinGecko universe (~13,000+ coins, ~500+ platforms). `base.assets` and `base.networks` are the **internal allow-lists** — only assets and networks explicitly promoted via `make allowlist-asset` / `make allowlist-network` are visible to pipelines and bots. This keeps trading operations scoped to a deliberate set of assets.

### Mutable table conventions

Every mutable table follows a consistent pattern:

- **Audit fields** — `created_at`, `updated_at`, `created_by`, `updated_by`
- **Soft delete** — `deleted_at`, `deleted_by` (DELETE intercepted by trigger → soft delete)
- **Versioning** — `version` counter + `sys_period` temporal range
- **History table** — `<table>_history` captures field-level diffs on every update/delete

Time-series tables (`asset_metrics_1d`, `asset_indicators_1d`, `price_observations`) are append/upsert only — no soft delete or versioning overhead.

### Migrations

| # | Migration | Description |
|---|---|---|
| 1 | `add_utilities` | `base` schema, audit / soft-delete / versioning trigger functions |
| 2 | `create_users` | System users table + seed admin user |
| 3 | `create_networks` | Blockchain networks and legacy networks (stock exchanges) |
| 4 | `create_assets` | Crypto assets — canonical + per-network deployments in one table |
| 5 | `create_data_sources` | External data source registry with rate-limit metadata |
| 6 | `create_asset_metrics_1d` | Daily OHLCV, market cap, circulating supply from data sources |
| 7 | `create_source_mappings` | Asset and network identifier mappings per external source |
| 8 | `create_venue_tables` | Exchange accounts / wallets, balances, transfers |
| 9 | `create_trading_tables` | Strategies, cycles, orders, executions, realised PnL |
| 10 | `create_price_observations` | Live price snapshots from the pricing bot (1–5 min cadence) |
| 11 | `create_capital_locks` | Capital reservation per active trading cycle + available balance view |
| 12 | `create_asset_metrics_intraday` | Intraday OHLCV (1m · 5m · 15m · 30m · 1h · 4h) |
| 13 | `create_system_events` | Bot operational event log (starts, stops, errors) |
| 14 | `create_portfolio_snapshots` | Daily portfolio valuation snapshots |
| 15 | `add_volume_to_price_observations` | Adds `volume_24h` column to price_observations |
| 16 | `fn_upsert_data_source` | Reusable `base.upsert_data_source()` PL/pgSQL function |
| 17 | `create_asset_indicators_1d` | Pre-computed daily technical indicators (ATR, RSI, MACD, Bollinger Bands, ...) |
| 18 | `create_trade_grid_levels` | Grid level rows per trade cycle (price, qty, status, fill price) |
| 19 | `add_stop_loss_price_to_trade_cycles` | Adds `stop_loss_price` column to trade_cycles |
| 20 | `add_tuning_fields_to_trade_grid_levels` | Adds ATR, weight, regime at time of level creation |
| 21 | `create_trade_dca_cycle_details` | Snapshot of full grid parameters per cycle |
| 22 | `create_backtest_runs` | Stores backtest results (metrics, parameters, date range) |
| 23 | `add_retention_policy_price_observations` | TimescaleDB hypertable + 90-day retention on price_observations |
| 24 | `create_coingecko_schema_raw_coins` | `coingecko` schema + `raw_coins` table (full CoinGecko coin list) |
| 25 | `create_coingecko_raw_platforms` | `coingecko.raw_platforms` table (asset platforms / blockchain networks) |

---

## Pipelines (Prefect 3)

All flows live in `apps/pipelines/src/flows/`. Deployments are declared in `apps/pipelines/prefect.yaml`.

| Flow | Schedule | Source | Target |
|---|---|---|---|
| `coingecko_sync_platforms_flow` | Mon 00:00 UTC | CoinGecko `/asset_platforms` | `coingecko.raw_platforms` |
| `coingecko_sync_coins_list_flow` | Mon 00:30 UTC | CoinGecko `/coins/list` | `coingecko.raw_coins` |
| `daily_pipeline_flow` | 02:00 UTC daily | CoinGecko `/coins/{id}/ohlc` + `/market_chart` | `base.asset_metrics_1d` → `base.asset_indicators_1d` |

The `daily_pipeline_flow` sequences two sub-flows in order:
1. `coingecko_fetch_ohlcv_1d_flow` — fetch yesterday's OHLCV + volume + market cap for all allow-listed assets
2. `compute_indicators_daily_flow` — recompute today's indicator row for all assets

### Technical Indicators Computed

| Category | Indicators |
|---|---|
| Volatility | ATR(14), ATR(20), ATR%, ATR SMA(20), volatility regime (`low` / `normal` / `high` / `extreme`) |
| Trend — MA | SMA(20), SMA(50), SMA(200), EMA(12), EMA(26) |
| Trend — MACD | MACD line, signal(9), histogram |
| Momentum | RSI(14) |
| Bands | Bollinger Bands(20, 2σ) — upper, middle, lower, width % |
| Volume | Volume SMA(20), volume ratio (today / 20d avg) |

---

## Bots

Located in `apps/bots/`. Each bot is a standalone asyncio polling script — no Prefect.

### Exchange Connection Layer

`apps/bots/common/connections/` provides a unified interface over `ccxt`:

```
BaseExchangeConnection  (abstract)
    └── CcxtRestConnection     (generic ccxt REST — works for any ccxt exchange)
            ├── CryptoComConnection  (Crypto.com override: fetch_tickers quirk)
            └── PaperTradingConnection  (wraps real connection, simulates fills)
```

Factory function:
```python
from common.connections import get_exchange
exchange = get_exchange("cryptocom")          # public endpoints, no key needed
exchange = get_exchange("binance", api_key=..., secret=...)
```

### Pricing Bot (`apps/bots/pricing_bot/`)

- Parameterised via CLI: `--exchange-id`, `--source-code`, `--pair` (repeatable)
- Default pairs: `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `CRO/USDT` on Crypto.com
- Writes bid, ask, last price, spread %, volume to `base.price_observations`
- `price_observations` is a TimescaleDB hypertable with 90-day retention policy

```bash
make pricing-bot
# or with custom pairs:
make pricing-bot exchange=binance source=exchange:binance pairs="btc/usdt eth/usdt"
```

### Trader Bot (`apps/bots/trader_bot/`)

Implements the **Volatility-Adaptive DCA Grid** strategy. One bot instance manages all active strategies concurrently, polling every 60 seconds.

**At startup:** syncs live maker/taker fees from the exchange via `fetch_trading_fees()` and updates `base.trade_strategies` if the values have changed. Falls back to DB values if the sync fails.

**Strategy logic:**

1. **Entry conditions** (checked once per cycle):
   - Price > SMA(200) — confirmed uptrend (configurable: `require_uptrend`)
   - SMA(50) > SMA(200) — golden cross (configurable: `require_golden_cross`)
   - RSI(14) < 60 — not overbought
   - ATR% < threshold — not entering during volatility spike
   - `force_entry: true` bypasses ALL conditions immediately

2. **Defensive mode** — when in a downtrend but RSI signals a bounce:
   - Triggers when: price < SMA200 **and** intraday RSI < `defensive_rsi_oversold` (default: 40)
   - Opens a wider, safer grid: larger ATR multiplier (0.8), higher profit target (2.5%), fewer levels (5)
   - Intraday RSI is fetched live from the exchange (1h candles, Wilder's smoothing)

3. **Grid calibration** — ATR-based, regime-aware:
   - `low` regime: ATR × 0.4 spacing, 1.0% profit target
   - `normal` regime: ATR × 0.5 spacing, 1.5% profit target
   - `high` regime: ATR × 0.7 spacing, 2.5% profit target
   - Grid levels get weighted capital allocation (deeper = more capital)
   - Quantity per level accounts for maker fee: `qty = capital / (price × (1 + maker_fee_pct))`

4. **Exit / stop loss**:
   - Take profit: price hits `avg_entry × (1 + profit_target%)`
   - Stop loss: price drops > `N × ATR` below `stop_loss_price`
   - Circuit breaker: ATR% > `circuit_breaker_atr_pct` — close cycle immediately

5. **Grid expansion** — if price drops past all grid levels, the bot can add expansion levels (configurable)

**Strategy parameters** are stored as JSONB in `base.trade_strategies.metadata` — edit via `make manage-trading`.

```bash
make trader-bot
```

---

## Backtesting

The backtest module (`apps/bots/backtest/`) simulates the DCA grid strategy on historical daily candles loaded from `base.asset_metrics_1d` and `base.asset_indicators_1d`.

### Single backtest run

```bash
uv run --env-file configs/envs/.env.local --project apps/bots \
    python -m backtest.runner \
    --asset-id 26 \
    --start 2023-01-01 \
    --end 2024-12-31 \
    --name "BTC 2023-2024 baseline" \
    --capital 10000
```

Results are printed to stdout and saved to `base.backtest_runs`.

### Parameter sweep

Run multiple named grid configs across multiple date windows:

```bash
# All configs × all windows
uv run --env-file configs/envs/.env.local --project apps/bots \
    python scripts/run_backtest_sweep.py

# Specific config and window
uv run --env-file configs/envs/.env.local --project apps/bots \
    python scripts/run_backtest_sweep.py \
    --config balanced --window bull_2020_2021 \
    --asset-id 26 --capital 10000 --no-save
```

**Built-in configs:** `balanced`, `conservative`, `aggressive`, `deep_grid`, `scalper`, `crash_hunter`

**Built-in windows:**

| Window | Range | Market |
|---|---|---|
| `bull_2020_2021` | 2020-01-01 → 2021-12-31 | Bull run |
| `bear_2022` | 2022-01-01 → 2022-12-31 | Bear market |
| `recovery_2023` | 2023-01-01 → 2023-12-31 | Recovery |
| `cycle_2023_2024` | 2023-01-01 → 2024-12-31 | Full cycle |
| `long_2020_2024` | 2020-01-01 → 2024-12-31 | Multi-cycle |

### Metrics

| Metric | Description |
|---|---|
| `total_return_pct` | Total % return over the period |
| `max_drawdown_pct` | Largest peak-to-trough equity drop |
| `win_rate` | % of cycles that closed profitable |
| `sharpe_ratio` | Annualised Sharpe (√252 scaling) |
| `profit_factor` | Gross profit ÷ gross loss |
| `total_cycles` | Number of completed grid cycles |
| `avg_dur_d` | Average cycle duration in days |

---

## Trading Management CLI

`scripts/manage_trading.py` is an interactive menu-driven CLI for managing strategies and active cycles without touching the DB directly.

```bash
make manage-trading
```

**Capabilities:**

- **Strategies** — list all, view details (with full parameter set), create new (guided wizard), edit parameters, change status (`ACTIVE` / `PAUSED` / `INACTIVE`), soft-delete
- **Cycles** — list open cycles per strategy, view cycle detail (grid levels, fill status, unrealised PnL), cancel or force-close a cycle

---

## Grafana Dashboards

Located in `grafana/`. Provisioned automatically when the container starts — no manual import needed.

| Dashboard | UID | Contents |
|---|---|---|
| Market Overview | `market-overview` | Live prices (pricing bot), daily OHLCV close, volume bars, latest OHLCV snapshot table |
| Technical Indicators | `technical-indicators` | RSI(14), ATR%, ATR absolute, price vs SMA50/SMA200, MACD, latest indicator snapshot table. Asset variable filter. |
| Trading Performance | `trading-performance` | Active strategy/cycle stat cards, open cycle table, grid level table, backtest results table, backtest return scatter |

**Datasource:** `inotives_db` — connects to TimescaleDB on `db:5432` using the same credentials from `.env.local`. Provisioned at `grafana/provisioning/datasources/postgres.yml`.

To add or modify dashboards, edit the JSON files in `grafana/dashboards/`. Changes are picked up within 30 seconds without restarting the container.

---

## Progress

### Done

- [x] Full database schema (25 migrations) — base + coingecko schemas, all tables, triggers live
- [x] DB utility functions — audit fields, soft delete, field-level versioning + history tables
- [x] `base.upsert_data_source()` reusable PL/pgSQL function
- [x] Docker Compose — TimescaleDB + Prefect 3 server + fetcher worker + Grafana
- [x] Prefect 3 deployment config via `prefect.yaml` with scheduled flows
- [x] Exchange connection layer — abstract base + generic ccxt REST + Crypto.com subclass
- [x] `PaperTradingConnection` — wraps real exchange, simulates fills for paper trading
- [x] `fetch_trading_fees()` — live maker/taker fee sync from exchange at bot startup
- [x] Pricing bot — parameterised CLI (`--exchange-id`, `--source-code`, `--pair`), writes to `price_observations`
- [x] `price_observations` — TimescaleDB hypertable with 90-day retention policy
- [x] CoinGecko raw schema — `coingecko.raw_coins` (full coin list, weekly sync) + `coingecko.raw_platforms` (platforms, weekly sync)
- [x] CoinGecko OHLCV pipeline — fetches O/H/L/C from `/ohlc` + volume and market cap from `/market_chart`
- [x] CoinGecko API key tiers — correct header (`x-cg-demo-api-key` vs `x-cg-pro-api-key`) selected from `COINGECKO_API_KEY_TYPE`
- [x] Daily pipeline — OHLCV fetch → indicator computation (sequenced Prefect flow)
- [x] Technical indicators — ATR, SMA, EMA, MACD, RSI, Bollinger Bands, Volume (via `pandas-ta`)
- [x] Asset allow-list model — `base.assets` + `base.networks` are curated allow-lists; CoinGecko universe lives in `coingecko.*`
- [x] `allowlist_asset.py` + `allowlist_network.py` — promote from CoinGecko raw tables to base allow-list
- [x] `make bootstrap` — full initial reference data setup (seed → sync CG → allow-list ETH/BTC/SOL)
- [x] BTC historical data (2010–2026) seeded + indicators backfilled
- [x] SOL historical data (2020–2026) seeded + indicators backfilled
- [x] Trader bot — volatility-adaptive DCA grid strategy (ATR-based spacing, weighted capital, regime-aware, fee-corrected quantities)
- [x] Defensive grid mode — enters with wider/safer grid when downtrend + RSI bounce detected
- [x] Intraday RSI — fetched live from exchange (1h candles, Wilder's smoothing) for defensive entry signals
- [x] Backtest engine — pure-Python simulation, daily candle feed, equity curve, all metrics
- [x] Backtest runner — CLI + DB loader, saves results to `base.backtest_runs`
- [x] Parameter sweep script — 6 configs × 5 date windows, tabular output
- [x] Paper trading setup — `setup_paper_trading.py` creates venue + BTC and SOL strategies
- [x] Trading management CLI — `manage_trading.py` interactive strategy/cycle CRUD
- [x] Grafana — auto-provisioned dashboards (Market Overview, Technical Indicators, Trading Performance)

### Planned

- [ ] **analytics (dbt)** — staging and mart models for metrics, portfolio performance
- [ ] **Prefect + dbt** — run dbt models as part of daily pipeline
- [ ] **Portfolio snapshots** — end-of-day automated valuation
- [ ] **CoinMarketCap OHLCV flow** — parallel data source for metrics
- [ ] **Additional strategies** — momentum, mean-reversion overlays

---

## Environment Files

| File | Purpose |
|---|---|
| `.env.example` | Template — copy to `.env.local` to get started |
| `.env.local` | Local development (gitignored) |
| `.env.prod` | Production (gitignored) |

All `.env.*` files (except `.env.example`) are gitignored. Never commit credentials.
