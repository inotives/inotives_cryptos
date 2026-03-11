# CLAUDE.md — Inotives Cryptos

This file is the primary context document for Claude Code. Read it fully before taking any action in this codebase. It covers what we're building, current state, architecture decisions, and coding conventions.

---

## What We Are Building

An **automated crypto trading system** — from raw data ingestion to live strategy execution.

The goal is a self-contained, fully automated pipeline that:
1. **Ingests** daily OHLCV market data (CoinGecko, CoinMarketCap) on a schedule
2. **Computes** technical indicators (ATR, RSI, MACD, Bollinger Bands, SMA/EMA) from raw data
3. **Monitors** live prices via exchange APIs (Crypto.com, Binance via `ccxt`)
4. **Executes** a **volatility-adaptive DCA grid strategy** — an evolution of standard DCA grid that uses ATR to dynamically size grid intervals, weights capital allocation across levels, and has market-regime-aware entry/exit signals
5. **Visualises** all data via Grafana dashboards (prices, indicators, trading performance, backtest results)

The system is personal/research-scale. No SaaS, no multi-user. Single PostgreSQL instance, Docker Compose, Python asyncio bots, Prefect 3 pipelines.

---

## Current Development State

### What is fully built and working

| Area | Status | Notes |
|---|---|---|
| Database schema (25 migrations) | ✅ Done | base + coingecko schemas, all tables, triggers live |
| CoinGecko raw schema | ✅ Done | coingecko.raw_coins + coingecko.raw_platforms, weekly sync |
| Asset allow-list model | ✅ Done | base.assets + base.networks are curated; CG universe in coingecko.* |
| Bootstrap setup | ✅ Done | `make bootstrap` seeds + syncs CG + allow-lists ETH/BTC/SOL |
| Docker Compose stack | ✅ Done | DB + Prefect server + worker + Grafana all healthy |
| Prefect deployments | ✅ Done | 3 flows scheduled (2 weekly CG syncs + daily pipeline) |
| Exchange connection layer | ✅ Done | ccxt REST wrapper + Crypto.com subclass + PaperTradingConnection |
| Live fee sync | ✅ Done | fetch_trading_fees() at bot startup, updates DB if changed |
| Pricing bot | ✅ Done | Parameterised CLI, polls tickers → price_observations (TimescaleDB hypertable, 90d retention) |
| CoinGecko OHLCV pipeline | ✅ Done | OHLC from /ohlc + volume/market_cap from /market_chart |
| Daily pipeline | ✅ Done | OHLCV → indicators, sequenced in one Prefect flow |
| Technical indicators | ✅ Done | ATR, SMA, EMA, MACD, RSI, Bollinger Bands, Volume |
| BTC historical data | ✅ Done | 2010–2026 in asset_metrics_1d + asset_indicators_1d |
| SOL historical data | ✅ Done | 2020–2026 in asset_metrics_1d + asset_indicators_1d |
| Trader bot | ✅ Done | Volatility-adaptive DCA grid, defensive mode, intraday RSI, fee-corrected quantities |
| Backtest engine + runner | ✅ Done | Simulates DCA grid on historical daily candles, saves to DB |
| Parameter sweep script | ✅ Done | 6 configs × 5 date windows, tabular output |
| Paper trading setup | ✅ Done | BTC/USDT + SOL/USDT strategies on Crypto.com (Paper) venue |
| Trading management CLI | ✅ Done | Interactive CRUD for strategies and cycles |
| Grafana dashboards | ✅ Done | Market Overview, Technical Indicators, Trading Performance |

### What is next / planned

- **dbt analytics** — staging + mart models for metrics and portfolio performance
- **Prefect + dbt integration** — run dbt as part of daily pipeline
- **Portfolio snapshots** — end-of-day automated valuation
- **CoinMarketCap OHLCV flow** — parallel data source using existing CMC client

---

## Architecture & Stack

```
Data Sources (CoinGecko, CoinMarketCap, ccxt exchanges)
      │
      ▼
apps/pipelines  (Prefect 3)
  · coingecko-platforms-weekly    Mon 00:00 UTC → coingecko.raw_platforms
  · coingecko-coins-list-weekly   Mon 00:30 UTC → coingecko.raw_coins
  · daily-data-pipeline           02:00 UTC     → asset_metrics_1d → asset_indicators_1d
      │
      ▼
PostgreSQL / TimescaleDB
  coingecko schema  (raw CoinGecko universe — reference library)
    · raw_coins · raw_platforms
  base schema  (curated internal allow-list + all trading data)
    · networks · assets · asset_source_mappings
    · asset_metrics_1d · asset_indicators_1d
    · price_observations (hypertable, 90d retention)
    · trade_* · capital_locks · backtest_runs
      │                   │
      ▼                   ▼
analytics (dbt)     apps/bots (asyncio)
                      pricing_bot   → base.price_observations
                      trader_bot    → base.trade_* tables
                      backtest      → base.backtest_runs
      │
      ▼
Grafana (port 3030)
  · Market Overview · Technical Indicators · Trading Performance
```

**Stack:**
- `uv` — package manager and workspace tool
- `asyncpg` — async PostgreSQL driver (used everywhere for DB access)
- `ccxt` — unified crypto exchange library
- `pandas` + `pandas-ta` — indicator computation in pipelines
- `pydantic-settings` — config loading from `.env.*` files
- `prefect 3` — flow orchestration with process worker pool
- `dbmate` — SQL-first migrations (invoked via `uvx`)
- `grafana` — BI dashboards, auto-provisioned via `grafana/provisioning/`
- `docker compose` — local infrastructure

---

## Folder Structure

```
inotives_cryptos/
├── analytics/                  # dbt project — NOT modified via pipelines directly
├── apps/
│   ├── bots/                   # asyncio polling scripts (NOT Prefect)
│   │   ├── common/
│   │   │   ├── config.py       # pydantic Settings — reads from .env.local
│   │   │   ├── db.py           # asyncpg connection pool (init_pool / get_conn)
│   │   │   └── connections/    # Exchange connection layer
│   │   │       ├── base.py     # Abstract BaseExchangeConnection + TypedDicts (Ticker, TradingFees, ...)
│   │   │       ├── ccxt_rest.py        # Generic ccxt REST + fetch_trading_fees()
│   │   │       ├── paper.py            # PaperTradingConnection (simulated fills)
│   │   │       ├── __init__.py         # get_exchange() factory
│   │   │       └── exchanges/
│   │   │           └── cryptocom.py    # Crypto.com override (fetch_tickers quirk)
│   │   ├── backtest/
│   │   │   ├── engine.py       # Pure-Python DCA grid simulation engine
│   │   │   ├── runner.py       # DB loader + CLI entry point
│   │   │   └── models.py       # BacktestCandle dataclass
│   │   ├── pricing_bot/main.py # CLI: --exchange-id --source-code --pair (repeatable)
│   │   ├── trader_bot/
│   │   │   ├── main.py         # Bot entry point — fee sync at startup, polling loop
│   │   │   └── strategies/
│   │   │       ├── base.py     # Abstract base strategy
│   │   │       └── dca_grid.py # DcaGridStrategy — full implementation
│   │   └── tests/              # pytest suite (backtest engine + strategy unit tests)
│   └── pipelines/              # All Prefect flows live here
│       ├── prefect.yaml        # Deployment definitions + schedules (source of truth)
│       └── src/
│           ├── api/
│           │   ├── coingecko.py        # CoinGeckoClient (demo/pro key tier support)
│           │   └── coinmarketcap.py    # CoinMarketCapClient
│           ├── config.py       # pydantic Settings — reads from .env.local
│           ├── main.py         # Convenience import check
│           └── flows/
│               ├── coingecko_platforms.py  # Weekly: /asset_platforms → coingecko.raw_platforms
│               ├── coingecko_coins_list.py # Weekly: /coins/list → coingecko.raw_coins
│               ├── coingecko_ohlcv_1d.py   # Daily OHLCV fetch (/ohlc + /market_chart)
│               ├── compute_indicators_1d.py # Indicator computation (backfill + daily)
│               └── daily_pipeline.py       # Orchestrator: OHLCV → indicators
├── configs/envs/
│   ├── .env.example            # Template (committed)
│   └── .env.local              # Local secrets (gitignored — never commit)
├── db/
│   ├── init/01_create_databases.sh  # Creates prefect_internal DB on first start
│   ├── migrations/             # 25 dbmate SQL files
│   └── seeds/                  # CSV data files for seeding
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/postgres.yml  # Auto-configures TimescaleDB datasource
│   │   └── dashboards/provider.yml   # Points Grafana at the dashboards folder
│   └── dashboards/
│       ├── market_overview.json
│       ├── technical_indicators.json
│       └── trading_performance.json
├── scripts/
│   ├── seed_data_sources_from_csv.py # Seed base.data_sources (run once or via bootstrap)
│   ├── seed_metrics_1d_from_csv.py   # Historical OHLCV backfill from CMC CSV
│   ├── allowlist_asset.py      # Promote coingecko.raw_coins entry → base.assets
│   ├── allowlist_network.py    # Promote coingecko.raw_platforms entry → base.networks
│   ├── setup_paper_trading.py  # Creates paper trading venue + BTC/SOL strategies
│   ├── manage_trading.py       # Interactive CLI for strategy/cycle CRUD
│   └── run_backtest_sweep.py   # Sweep 6 configs × 5 date windows
├── docker-compose.yml
├── Makefile
└── pyproject.toml              # uv workspace root
```

**Key rules:**
- `apps/bots/` — asyncio only. No Prefect imports.
- `apps/pipelines/` — all scheduled/orchestrated work lives here.
- `analytics/` — dbt project, standalone. Never import from pipelines.
- Never hardcode credentials. Always use `settings.*` from pydantic config.
- Never use `pip install`. Always use `uv add` inside the relevant app folder.

---

## Database

### Schema layout

| Schema | Purpose |
|---|---|
| `coingecko` | Raw data landed directly from CoinGecko API — full universe, no curation. Raw tables only — no audit triggers, no soft delete. |
| `base` | Internal curated data — trading allow-list, metrics, indicators, trading state. Full audit/versioning on mutable tables. |

### Asset allow-list model

`coingecko.raw_coins` and `coingecko.raw_platforms` hold the full CoinGecko universe. `base.assets` and `base.networks` are the **internal allow-lists** — only assets and networks explicitly promoted via `allowlist_asset.py` / `allowlist_network.py` are visible to pipelines and bots.

### Schema conventions (base schema)

All tables live in the `base` schema. `public` is never used for app tables.

**Mutable tables** (reference data, trading state) must have:
- Audit fields: `created_at`, `updated_at`, `created_by`, `updated_by`
- Soft delete: `deleted_at`, `deleted_by` + `chk_deleted_fields_*` CHECK constraint
- Versioning: `version INTEGER`, `sys_period TSTZRANGE`
- History table: `base.<table>_history` with `changed_at`, `changed_by`, `change_type`, `changes`
- 3 triggers: `auditing_trigger_*`, `soft_delete_trigger_*`, `versioning_trigger_*`

**Append-only tables** (time-series, events) do NOT need soft delete or versioning:
- `base.asset_metrics_1d`, `base.asset_indicators_1d`
- `base.price_observations`, `base.asset_metrics_intraday`
- `base.trade_executions`, `base.trade_pnl`, `base.system_events`, `base.portfolio_snapshots`

**Raw tables** (`coingecko` schema) — no audit triggers, no soft delete, no versioning. Upsert-only.

### Type conventions
- Crypto prices: `NUMERIC(36, 18)`
- USD aggregates (volume, market cap): `NUMERIC(36, 2)`
- Percentages and ratios: `NUMERIC(10, 6)`
- Timestamps: always `TIMESTAMPTZ`
- ENUM types must be schema-qualified: `base.<type_name>`
- Nullable unique columns → use partial unique indexes, NOT UNIQUE constraints

### Migration rules
- One logical change per file. Atomic.
- Always include `-- migrate:up` and `-- migrate:down` blocks
- Down block must use `DROP TABLE IF EXISTS ... CASCADE`
- Create with: `make migrate-new name=<description>`

### Key tables

| Table | Purpose |
|---|---|
| `coingecko.raw_coins` | Full CoinGecko coin list (~13k+ coins). Synced weekly. PK: `coingecko_id`. |
| `coingecko.raw_platforms` | Full CoinGecko asset platforms list. Synced weekly. PK: `coingecko_id`. |
| `base.assets` | Allow-listed assets for trading/tracking. Promoted from `coingecko.raw_coins` via `allowlist_asset.py`. |
| `base.networks` | Allow-listed blockchain networks. Promoted from `coingecko.raw_platforms` via `allowlist_network.py`. |
| `base.data_sources` | External data source registry (CoinGecko, CMC, exchanges). Has `upsert_data_source()` function. |
| `base.asset_source_mappings` | Maps internal `asset_id` → external identifier per source (e.g. BTC → `"bitcoin"` on CoinGecko). |
| `base.asset_metrics_1d` | Daily OHLCV + market cap + supply. One row per (asset, date, source). `is_final=true` for completed days. |
| `base.asset_indicators_1d` | Pre-computed daily indicators. Populated by `compute_indicators_1d.py`. One row per (asset, date). |
| `base.price_observations` | Live ticker snapshots from pricing bot. TimescaleDB hypertable, 90-day retention. |
| `base.trade_strategies` | Strategy config (type, parameters, capital allocation, live fees). Parameters stored as JSONB in `metadata`. |
| `base.trade_cycles` | One active cycle per strategy. Tracks grid state, avg entry, stop loss, unrealised PnL. |
| `base.trade_grid_levels` | Individual grid level rows per cycle (price, qty, weight, fill status). |
| `base.trade_orders` | All placed orders (open, filled, cancelled). |
| `base.capital_locks` | Capital reserved per active cycle. View `venue_available_capital` shows free balance. |
| `base.backtest_runs` | Results of backtest simulations (metrics, parameters, date range, status). |

---

## Prefect Pipelines

### Deployments (defined in `apps/pipelines/prefect.yaml`)

| Deployment name | Schedule | Flow |
|---|---|---|
| `coingecko-platforms-weekly` | Mon 00:00 UTC | `coingecko_platforms.py:coingecko_sync_platforms_flow` |
| `coingecko-coins-list-weekly` | Mon 00:30 UTC | `coingecko_coins_list.py:coingecko_sync_coins_list_flow` |
| `daily-data-pipeline` | 02:00 UTC daily | `daily_pipeline.py:daily_pipeline_flow` |

### Daily pipeline flow sequence
```
daily_pipeline_flow(target_date)
  ├── coingecko_fetch_ohlcv_1d_flow(target_date)
  │     ├── load_asset_mappings()          # query asset_source_mappings for CoinGecko IDs
  │     ├── fetch_ohlcv_for_asset()        # GET /coins/{id}/ohlc?days=90 per asset
  │     ├── fetch_market_chart_for_asset() # GET /coins/{id}/market_chart?days=91 per asset
  │     └── upsert_ohlcv()                 # → base.asset_metrics_1d (OHLC + volume + market_cap)
  └── compute_indicators_daily_flow()
        ├── load_ohlcv()                   # last 400 days from asset_metrics_1d
        ├── compute_indicators()           # pandas-ta: ATR, SMA, EMA, MACD, RSI, BB, Volume
        └── upsert_indicators(target_dates=[today])  # → base.asset_indicators_1d
```

### Registering / updating deployments
```bash
make prefect-deploy   # runs: cd apps/pipelines && prefect --no-prompt deploy --all
```
Worker pool: `data-eng-pool` (process type). Worker runs in `inotives_fetcher_worker` container.

### Important: prefect_internal database
The `prefect_internal` PostgreSQL database is created by `db/init/01_create_databases.sh` on first container start. If the DB volume already exists (e.g. after recreating containers), the init script does not re-run. Create manually if missing:
```bash
docker exec inotives_db psql -U postgres -c "CREATE DATABASE prefect_internal;"
```

---

## Exchange Connection Layer

Located in `apps/bots/common/connections/`.

```python
# Factory — use this everywhere in bots
from common.connections import get_exchange
exchange = get_exchange("cryptocom")                               # public, no key
exchange = get_exchange("cryptocom", api_key=..., secret=...)     # authenticated
exchange = get_exchange("binance", api_key=..., secret=...)       # any ccxt exchange
```

**Class hierarchy:**
```
BaseExchangeConnection  (abc, base.py)
    └── CcxtRestConnection  (ccxt_rest.py)  ← generic, works for all ccxt exchanges
            ├── CryptoComConnection  (exchanges/cryptocom.py)
            │     override: fetch_tickers() — public endpoint doesn't support symbol filtering;
            │               fetches all tickers, filters client-side
            └── PaperTradingConnection  (paper.py)
                  wraps any real connection; simulates instant fills, no real orders placed
```

**Normalised return types** (`base.py`):
- `Ticker` — symbol, last, bid, ask, spread_pct, volume_24h, timestamp
- `OrderBook` — symbol, bids, asks, timestamp
- `Candle` — timestamp, open, high, low, close, volume
- `TradingFees` — symbol, maker, taker

`volume_24h` on Crypto.com public API is NULL — base class approximates from `baseVolume * last`.

---

## Trader Bot

### Strategy: Volatility-Adaptive DCA Grid

Implemented in `apps/bots/trader_bot/strategies/dca_grid.py`. The bot loads all `ACTIVE` strategies from `base.trade_strategies` and runs them concurrently in a 60-second polling loop.

**Fee sync at startup:**
`sync_strategy_fees()` in `trader_bot/main.py` calls `exchange.fetch_trading_fees(symbol)` for each unique symbol, updates `base.trade_strategies` (column + JSONB metadata) if the values have changed, and updates the in-memory strategy dict for the current tick. Falls back to DB values if sync fails.

Crypto.com actual fees: 0.25% maker (`0.0025`) / 0.50% taker (`0.005`).

**Grid quantity calculation:**
`quantity = capital_allocated / (target_price × (1 + maker_fee_pct))` — ensures capital covers the fee cost.

**Entry logic (`_maybe_open_cycle`):**
1. Check `force_entry` flag — if `true`, bypass all conditions and open immediately
2. Run `_check_entry_conditions` — requires: price > SMA(200), SMA(50) > SMA(200), RSI < 60, ATR% < threshold
3. If normal entry fails, check **defensive mode** — fetch intraday RSI, check bounce signal

**Grid calibration (ATR-based, regime-aware):**
- `low` regime: ATR × 0.4, profit target 1.0%
- `normal` regime: ATR × 0.5, profit target 1.5%
- `high` regime: ATR × 0.7, profit target 2.5%

**Defensive grid mode:**
- Activates in downtrend when intraday RSI < `defensive_rsi_oversold` (default 40)
- Wider grid: ATR × 0.8, profit target 2.5%, 5 levels, equal weights
- Intraday RSI computed via Wilder's method from live hourly candles

**Exit logic:**
- Take profit: `avg_entry_price × (1 + profit_target%)`
- Stop loss: price < `stop_loss_price` (set at `lowest_filled_level - N × ATR`)
- Circuit breaker: ATR% > `circuit_breaker_atr_pct`

---

## Backtest

Located in `apps/bots/backtest/`.

- `engine.py` — `DcaGridBacktestEngine`: pure-Python simulation, no DB. Tracks equity curve, fills limit buys when `candle.low <= target_price`, fires take profit when `candle.high >= target_sell`.
- `runner.py` — loads candles from `base.asset_metrics_1d` and indicators from `base.asset_indicators_1d`, runs the engine, saves results to `base.backtest_runs`. Default fees: maker=0.0025, taker=0.005.
- `models.py` — `BacktestCandle` dataclass.

**Sweep script** (`scripts/run_backtest_sweep.py`):
- 6 named configs: `balanced`, `conservative`, `aggressive`, `deep_grid`, `scalper`, `crash_hunter`
- 5 date windows: `bull_2020_2021`, `bear_2022`, `recovery_2023`, `cycle_2023_2024`, `long_2020_2024`

---

## Grafana

Located in `grafana/`. Port **3030** (host) → 3000 (container). Login: `admin` / `admin` (override via `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` in `.env.local`).

Datasource and dashboards are fully auto-provisioned on container start — no manual setup.

**Datasource** (`grafana/provisioning/datasources/postgres.yml`):
- Type: PostgreSQL (TimescaleDB)
- UID: `inotives_timescaledb`
- Credentials injected via env vars (`${DB_USER}`, `${DB_PASSWORD}`, `${DB_NAME}`)
- `database` field must be in `jsonData` (not top-level) — required by Grafana 10+

**Dashboards** (`grafana/dashboards/`):

| File | UID | Contents |
|---|---|---|
| `market_overview.json` | `market-overview` | Live prices, daily close, volume bars, OHLCV snapshot |
| `technical_indicators.json` | `technical-indicators` | RSI, ATR%, ATR absolute, price vs SMA50/200, MACD, indicator snapshot. Asset variable filter. |
| `trading_performance.json` | `trading-performance` | Active strategies/cycles (stat cards), open cycle table, grid level table, backtest results table + scatter |

To add dashboards: drop a `.json` file into `grafana/dashboards/` — picked up within 30s.

---

## Reference Data Setup

### Bootstrap (fresh environment)

```bash
make bootstrap   # seeds data sources + syncs CG + allow-lists ETH/BTC/SOL
```

Internally runs in sequence:
1. `make seed-data-sources` — seeds `base.data_sources` from CSV
2. `make sync-coingecko-platforms` — populates `coingecko.raw_platforms` directly (no Prefect)
3. `make sync-coingecko-coins` — populates `coingecko.raw_coins` directly (no Prefect)
4. `make allowlist-network` × 3 — ethereum, bitcoin, solana
5. `make allowlist-asset` × 3 — bitcoin, ethereum, solana

> ETH/BTC/SOL are the default starting point. Edit the `bootstrap` target in Makefile or run `allowlist-*` individually to customise.

### Adding assets / networks after bootstrap

```bash
make allowlist-asset   coingecko_id=dogecoin cmc_id=74
make allowlist-network coingecko_id=binance-smart-chain code=BSC
```

`coingecko.raw_coins` and `coingecko.raw_platforms` must be populated first (either via `make bootstrap` or the weekly Prefect flows).

### Historical OHLCV backfill

```bash
make seed-metrics-1d csv=db/seeds/btc_historical_data_20100513-20260309.csv

uv run --env-file configs/envs/.env.local --project apps/pipelines python - <<'EOF'
import asyncio, sys
sys.path.insert(0, "apps/pipelines")
from src.flows.compute_indicators_1d import compute_indicators_backfill_flow
asyncio.run(compute_indicators_backfill_flow(asset_codes=["btc"]))
EOF
```

---

## Makefile Commands

```bash
# Services
make services-up                    # Start DB + Prefect + worker + Grafana containers
make services-down                  # Stop all containers

# Migrations
make migrate-up                     # Apply all pending migrations
make migrate-down                   # Roll back last migration
make migrate-status                 # Show migration state
make migrate-new name=<name>        # Create new migration file

# Bootstrap
make bootstrap                      # Full initial setup (seed + sync CG + allow-list defaults)
make sync-coingecko-platforms       # Manually run platform sync (bypasses Prefect)
make sync-coingecko-coins           # Manually run coin list sync (bypasses Prefect)

# Allow-listing
make allowlist-asset coingecko_id=<id>              # Promote coin to base.assets
make allowlist-asset coingecko_id=<id> cmc_id=<id>  # With CMC mapping
make allowlist-asset-dry coingecko_id=<id>          # Preview only
make allowlist-network coingecko_id=<id>            # Promote platform to base.networks
make allowlist-network-dry coingecko_id=<id>        # Preview only

# Seeding
make seed-data-sources              # Seed base.data_sources from CSV (required once)
make seed-metrics-1d csv=<path>     # Import daily OHLCV from CMC CSV export

# Bots
make pricing-bot                    # Start the pricing bot
make trader-bot                     # Start the trader bot
make setup-paper-trading            # Create paper trading venue + strategies in DB
make manage-trading                 # Interactive CLI for strategy/cycle CRUD

# Grafana
make grafana-ui                     # Open http://localhost:3030

# Prefect
make prefect-deploy                 # Register/update flow deployments
make prefect-ui                     # Open http://localhost:4200

# Init
make init                           # Create venv + install all deps (fresh clone)
```

---

## Config Fields (pydantic Settings)

Both `apps/bots/common/config.py` and `apps/pipelines/src/config.py` use the same env var names:

| Field | Env var | Notes |
|---|---|---|
| `db_host` | `DB_HOST` | `localhost` for local, `db` inside Docker |
| `db_port` | `DB_PORT` | `5435` for host access, `5432` inside Docker |
| `db_user` | `DB_USER` | |
| `db_password` | `DB_PASSWORD` | Note: field is `db_password`, NOT `db_pass` |
| `db_name` | `DB_NAME` | |
| `cryptocom_api_key` | `CRYPTOCOM_API_KEY` | Optional — public endpoints work without it |
| `cryptocom_api_secret` | `CRYPTOCOM_API_SECRET` | |
| `binance_api_key` | `BINANCE_API_KEY` | |
| `binance_api_secret` | `BINANCE_API_SECRET` | |
| `coingecko_api_key` | `COINGECKO_API_KEY` | Optional — free tier works |
| `coingecko_api_key_type` | `COINGECKO_API_KEY_TYPE` | `"demo"` (default) or `"pro"` — controls which auth header is used |
| `coinmarketcap_api_key` | `COINMARKETCAP_API_KEY` | |

Grafana credentials are not loaded by pydantic — they go directly to the Docker container via `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` env vars.

---

## Git Workflow

- **Personal GitHub**: `inotives` — `inotives@gmail.com`
- **SSH alias**: `github-personal` → `~/.ssh/id_ed25519_inotives` with `IdentitiesOnly yes`
- **Branch naming**: `INO-XXXX/<description>` for tracked work, `feat-<description>` for features
- **Commit scope**: keep migrations separate from app code
- **Local git identity** (already configured for this repo):
  ```bash
  git config --local user.email "inotives@gmail.com"
  git config --local user.name "inotives"
  ```
- **Current branch**: `INO-0002/pricing-bots`
- **Merged**: `feat-initial-db-migration` (all 17 migrations), `INO-0001/seeding-the-tables`

---

## Known Gotchas

- **`db_password` not `db_pass`** — pydantic field was renamed to match `DB_PASSWORD` env var. Do not revert.
- **`prefect_internal` DB** — only created on first container init. Must be created manually if containers are recreated with an existing volume. See Prefect section above.
- **CoinGecko OHLCV granularity** — free tier `/ohlc?days=N` returns daily candles only when `days >= 90`. Flow uses `days=90` and filters to the target date.
- **CoinGecko market chart granularity** — free/demo tier requires `days > 90` for daily granularity. Flow uses `days=91`. Pro tier sends `interval=daily` explicitly.
- **CoinGecko API key headers** — Demo key uses `x-cg-demo-api-key`. Pro key uses `x-cg-pro-api-key`. Using the wrong header silently ignores the key. Controlled by `COINGECKO_API_KEY_TYPE` env var.
- **`allowlist_asset` / `allowlist_network` require raw tables populated first** — `coingecko.raw_coins` and `coingecko.raw_platforms` must be synced before running allowlist scripts. `make bootstrap` handles this automatically.
- **Crypto.com `fetch_tickers`** — public endpoint doesn't accept a symbol list. `CryptoComConnection.fetch_tickers()` fetches all tickers and filters client-side.
- **`volume_24h` on Crypto.com** — public ticker doesn't return `quoteVolume`. `CcxtRestConnection._normalise_ticker()` approximates it as `baseVolume * last`.
- **Crypto.com fees** — actual rates: 0.25% maker (`0.0025`) / 0.50% taker (`0.005`). Bot syncs live fees at startup via `fetch_trading_fees()`.
- **`pandas-ta` install order** — Dockerfile installs `numpy` and `pandas` first, then the full project, to avoid build-time import errors in `pandas-ta`.
- **`uv` venv warning** — running `uv run --project apps/pipelines` from the root shows a venv mismatch warning. This is harmless — the correct project venv is used.
- **`metadata` JSONB from asyncpg** — asyncpg returns JSONB columns as raw strings. Always wrap with `json.loads()` when `isinstance(value, str)`. See `trader_bot/main.py:load_active_strategies()`.
- **`asset_metrics_1d` volume column** — column is named `volume_usd`, not `volume`. Backtest runner and any raw SQL must use `volume_usd`.
- **Grafana datasource provisioning** — `database` field must go inside `jsonData` (not top-level) for Grafana 10+. Use `${VAR}` syntax (not `$VAR`) for env var substitution in provisioning YAML.
- **Grafana port** — mapped to host port **3030** (not 3000, which is used by local Dagster). Container-internal port is still 3000.
- **TimescaleDB hypertable PK** — `price_observations` hypertable required `(id, observed_at)` composite PK. The original `id`-only PK was dropped and recreated in migration 23.
