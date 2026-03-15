# CLAUDE.md — Inotives Cryptos

This file is the primary context document for Claude Code. Read it fully before taking any action in this codebase. It covers what we're building, current state, architecture decisions, and coding conventions.

---

## What We Are Building

An **automated crypto trading system** — from raw data ingestion to live strategy execution.

The goal is a self-contained, fully automated pipeline that:
1. **Ingests** daily OHLCV market data (CoinGecko) on a schedule (cron)
2. **Computes** technical indicators (ATR, RSI, MACD, Bollinger Bands, SMA/EMA, EMA50/200, ADX, regime scores) from raw data
3. **Monitors** live prices via exchange APIs (Crypto.com, Binance via `ccxt`)
4. **Executes** a **Hybrid Grid + Regime-Switching strategy** — DCA Grid for sideways markets, Trend Following for uptrends, with a regime score (0–100) dynamically allocating capital between them

The system is personal/research-scale. No SaaS, no multi-user. Runs its own PostgreSQL (standalone Docker) or can share an existing instance (e.g. `inotives_aibots` project). Python asyncio bots, cron-scheduled data pipeline.

---

## Current Development State

### What is fully built and working

| Area | Status | Notes |
|---|---|---|
| Database schema (27 migrations) | ✅ Done | inotives_tradings + coingecko schemas, all tables, triggers live |
| CoinGecko raw schema | ✅ Done | coingecko.raw_coins + coingecko.raw_platforms, weekly sync |
| Asset allow-list model | ✅ Done | inotives_tradings.assets + inotives_tradings.networks are curated; CG universe in coingecko.* |
| Bootstrap setup | ✅ Done | `make bootstrap` seeds + syncs CG + allow-lists ETH/BTC/SOL |
| Exchange connection layer | ✅ Done | ccxt REST wrapper + Crypto.com subclass + PaperTradingConnection |
| Live fee sync | ✅ Done | fetch_trading_fees() at bot startup, updates DB if changed |
| Pricing bot | ✅ Done | Parameterised CLI, polls tickers → price_observations |
| CoinGecko OHLCV pipeline | ✅ Done | OHLC from /ohlc + volume/market_cap from /market_chart |
| Daily data pipeline | ✅ Done | OHLCV → indicators → regime scores, sequenced in `bots/data_bot/main.py` |
| Technical indicators | ✅ Done | ATR, SMA, EMA(12/26/50/200), MACD, RSI, BB, Volume, ADX, EMA slope 5d, vol ratio |
| Regime indicators | ✅ Done | EMA50/200, ADX(14), EMA slope 5d%, vol ratio in asset_indicators_1d |
| Market regime scores | ✅ Done | inotives_tradings.asset_market_regime — component + final RS (0–100), all assets backfilled |
| BTC historical data | ✅ Done | 2010–2026 in asset_metrics_1d + asset_indicators_1d |
| All 6 assets historical data | ✅ Done | BTC/ETH/SOL/ADA/CRO/XRP indicators + regime scores backfilled |
| DCA Grid trader bot | ✅ Done | Volatility-adaptive, defensive mode, intraday RSI, fee-corrected quantities |
| Trend Following strategy | ✅ Done | EMA cross + 5-day high breakout entry, ATR-scaled sizing, rising trailing stop |
| Hybrid capital coordinator | ✅ Done | Regime-based sliding scale, priority rules, intraday circuit breaker |
| Paper trading setup | ✅ Done | BTC/USDT + SOL/USDT strategies on Crypto.com (Paper) venue |
| Trading management CLI | ✅ Done | Interactive CRUD for strategies and cycles |

### What is next / planned

- **Portfolio snapshots** — end-of-day automated valuation
- **CoinMarketCap OHLCV flow** — parallel data source using existing CMC client

---

## Architecture & Stack

```
Data Sources (CoinGecko, ccxt exchanges)
      │
      ▼
common/data/  (cron-scheduled or manual)
  · coingecko_sync.py   → coingecko.raw_platforms + coingecko.raw_coins
  · ohlcv.py            → inotives_tradings.asset_metrics_1d
  · indicators.py       → inotives_tradings.asset_indicators_1d
  · market_regime.py    → inotives_tradings.asset_market_regime
bots/data_bot/main.py  (02:00 UTC cron → OHLCV → indicators → regime)
      │
      ▼
PostgreSQL (standalone Docker or shared instance)
  coingecko schema  (raw CoinGecko universe — reference library)
    · raw_coins · raw_platforms
  inotives_tradings schema  (curated internal allow-list + all trading data)
    · networks · assets · asset_source_mappings
    · asset_metrics_1d · asset_indicators_1d
    · price_observations · trade_* · capital_locks
      │
      ▼
bots/ (asyncio)
  pricing_bot   → inotives_tradings.price_observations
  trader_bot    → inotives_tradings.trade_* tables
```

**Stack:**
- `uv` — package manager and workspace tool
- `asyncpg` — async PostgreSQL driver (used everywhere for DB access)
- `ccxt` — unified crypto exchange library
- `requests` — HTTP client (CoinGecko API)
- `pandas` + `pandas-ta` — indicator computation
- `pydantic-settings` — config loading from `.env.*` files
- `dbmate` — SQL-first migrations (invoked via `uvx`)

**Infrastructure:**
- PostgreSQL — standalone Docker (`make db-up`) or shared with `inotives_aibots` project
- `make db-ensure` auto-detects: uses existing DB if reachable, otherwise starts local Docker PostgreSQL (pgvector image)
- DB connection configured via `.env.local`

---

## Folder Structure

```
inotives/
├── common/
│   ├── config.py           # pydantic Settings — reads from .env.local
│   ├── db.py               # asyncpg connection pool (init_pool / get_conn / is_pool_initialized)
│   ├── connections/        # Exchange connection layer
│   │   ├── base.py         # Abstract BaseExchangeConnection + TypedDicts (Ticker, TradingFees, ...)
│   │   ├── ccxt_rest.py    # Generic ccxt REST + fetch_trading_fees()
│   │   ├── paper.py        # PaperTradingConnection (simulated fills)
│   │   ├── __init__.py     # get_exchange() factory
│   │   └── exchanges/
│   │       └── cryptocom.py    # Crypto.com override (fetch_tickers quirk)
│   ├── api/
│   │   ├── __init__.py
│   │   └── coingecko.py    # CoinGecko REST client (pure requests, no framework)
│   ├── data/
│   │   ├── __init__.py
│   │   ├── ohlcv.py            # Daily OHLCV fetch + upsert
│   │   ├── indicators.py       # Technical indicator computation
│   │   ├── market_regime.py    # Regime score computation
│   │   └── coingecko_sync.py   # CoinGecko reference sync (platforms + coins)
│   └── tools/
│       ├── __init__.py
│       ├── manage_assets.py    # Asset/network/source management CLI
│       ├── manage_trading.py   # Strategy/cycle management CLI
│       ├── manage_cron.py      # Cron job manager (install/remove/list)
│       ├── allowlist_asset.py  # Promote CoinGecko coin → allow-list
│       ├── allowlist_network.py # Promote CoinGecko platform → allow-list
│       └── setup_paper_trading.py  # Create paper trading venue + strategies
├── bots/
│   ├── data_bot/
│   │   └── main.py         # Daily pipeline: OHLCV → indicators → regime scores
│   ├── pricing_bot/
│   │   └── main.py         # CLI: --exchange-id --source-code --pair (repeatable)
│   └── trader_bot/
│       ├── main.py         # Bot entry point — fee sync at startup, polling loop
│       ├── hybrid_coordinator.py
│       └── strategies/
│           ├── __init__.py     # Strategy registry
│           ├── base.py         # Abstract base strategy
│           ├── dca_grid.py     # DcaGridStrategy — full implementation
│           └── trend_following.py  # TrendFollowingStrategy
├── tests/                  # pytest suite (strategy unit tests)
├── configs/envs/
│   ├── .env.example        # Template (committed)
│   └── .env.local          # Local secrets (gitignored — never commit)
├── db/
│   ├── migrations/         # dbmate SQL files
│   ├── scripts/            # Seeding scripts (data sources, metrics, assets, networks)
│   └── seeds/              # CSV data files for seeding
├── docker-compose.yml          # Standalone PostgreSQL (used when no external DB)
├── Makefile
└── pyproject.toml              # Single flat project (no workspace)
```

**Key rules:**
- All Python packages (`common/`, `bots/`) live at the project root. Bots are under `bots/data_bot/`, `bots/pricing_bot/`, and `bots/trader_bot/`.
- `common/data/` — data pipeline modules (OHLCV, indicators, regime). Usable standalone or from `bots/data_bot/main.py`.
- `common/api/` — API clients (CoinGecko). Pure `requests`, no async.
- `common/tools/` — management CLIs (assets, trading, cron, allow-listing, paper trading setup). All support `python -m common.tools.<name>` invocation.
- Never hardcode credentials. Always use `settings.*` from pydantic config.
- Never use `pip install`. Always use `uv add`.

---

## Database

### PostgreSQL setup

This project can run in two modes:
- **Standalone** — `make db-up` starts a local Docker PostgreSQL (pgvector image). `make db-down` stops it.
- **Shared** — connects to an existing PostgreSQL instance (e.g. the `inotives_aibots` project's DB on port 5445).

`make db-ensure` auto-detects: if the configured `DB_HOST:DB_PORT` is reachable, it uses that instance. Otherwise, it starts a local Docker PostgreSQL via `docker compose`. It also creates the `DB_NAME` database if it doesn't exist on the server (`make db-create`).

### Schema layout

| Schema | Purpose |
|---|---|
| `coingecko` | Raw data landed directly from CoinGecko API — full universe, no curation. Raw tables only — no audit triggers, no soft delete. |
| `inotives_tradings` | Internal curated data — trading allow-list, metrics, indicators, trading state. Full audit/versioning on mutable tables. |

### Asset allow-list model

`coingecko.raw_coins` and `coingecko.raw_platforms` hold the full CoinGecko universe. `inotives_tradings.assets` and `inotives_tradings.networks` are the **internal allow-lists** — only assets and networks explicitly promoted via `common/tools/allowlist_asset.py` / `common/tools/allowlist_network.py` (or `make allowlist-asset` / `make allowlist-network`) are visible to data modules and bots.

### Schema conventions (inotives_tradings schema)

All tables live in the `inotives_tradings` schema. `public` is never used for app tables.

**Mutable tables** (reference data, trading state) must have:
- Audit fields: `created_at`, `updated_at`, `created_by`, `updated_by`
- Soft delete: `deleted_at`, `deleted_by` + `chk_deleted_fields_*` CHECK constraint
- Versioning: `version INTEGER`, `sys_period TSTZRANGE`
- History table: `inotives_tradings.<table>_history` with `changed_at`, `changed_by`, `change_type`, `changes`
- 3 triggers: `auditing_trigger_*`, `soft_delete_trigger_*`, `versioning_trigger_*`

**Append-only tables** (time-series, events) do NOT need soft delete or versioning:
- `inotives_tradings.asset_metrics_1d`, `inotives_tradings.asset_indicators_1d`
- `inotives_tradings.price_observations`, `inotives_tradings.asset_metrics_intraday`
- `inotives_tradings.trade_executions`, `inotives_tradings.trade_pnl`, `inotives_tradings.system_events`, `inotives_tradings.portfolio_snapshots`

**Raw tables** (`coingecko` schema) — no audit triggers, no soft delete, no versioning. Upsert-only.

### Type conventions
- Crypto prices: `NUMERIC(36, 18)`
- USD aggregates (volume, market cap): `NUMERIC(36, 2)`
- Percentages and ratios: `NUMERIC(10, 6)`
- Timestamps: always `TIMESTAMPTZ`
- ENUM types must be schema-qualified: `inotives_tradings.<type_name>`
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
| `inotives_tradings.assets` | Allow-listed assets for trading/tracking. Promoted from `coingecko.raw_coins` via `common/tools/allowlist_asset.py`. |
| `inotives_tradings.networks` | Allow-listed blockchain networks. Promoted from `coingecko.raw_platforms` via `common/tools/allowlist_network.py`. |
| `inotives_tradings.data_sources` | External data source registry (CoinGecko, CMC, exchanges). Has `upsert_data_source()` function. |
| `inotives_tradings.asset_source_mappings` | Maps internal `asset_id` → external identifier per source (e.g. BTC → `"bitcoin"` on CoinGecko). |
| `inotives_tradings.asset_metrics_1d` | Daily OHLCV + market cap + supply. One row per (asset, date, source). `is_final=true` for completed days. |
| `inotives_tradings.asset_indicators_1d` | Pre-computed daily indicators. Populated by `common/data/indicators.py`. One row per (asset, date). Includes EMA50/200, ADX(14), ema_slope_5d, vol_ratio_14 for regime detection. |
| `inotives_tradings.asset_market_regime` | Daily regime scores per asset. Computed by `common/data/market_regime.py`. Columns: raw_adx/slope/vol_ratio + score_adx/slope/vol (0–100 each) + final_regime_score (weighted). |
| `inotives_tradings.price_observations` | Live ticker snapshots from pricing bot. Composite PK `(id, observed_at)`. |
| `inotives_tradings.trade_strategies` | Strategy config (type, parameters, capital allocation, live fees). Parameters stored as JSONB in `metadata`. |
| `inotives_tradings.trade_cycles` | One active cycle per strategy. Tracks grid state, avg entry, stop loss, unrealised PnL. |
| `inotives_tradings.trade_grid_levels` | Individual grid level rows per cycle (price, qty, weight, fill status). |
| `inotives_tradings.trade_orders` | All placed orders (open, filled, cancelled). |
| `inotives_tradings.capital_locks` | Capital reserved per active cycle. View `venue_available_capital` shows free balance. |

---

## Daily Data Pipeline

The daily data pipeline runs as a simple Python script (`bots/data_bot/main.py`), intended to be scheduled via cron.

### Pipeline sequence
```
data_bot.main(target_date)
  ├── run_ohlcv_fetch(target_date)        # common/data/ohlcv.py
  │     ├── load_asset_mappings()          # query asset_source_mappings for CoinGecko IDs
  │     ├── fetch_ohlcv_for_asset()        # GET /coins/{id}/ohlc?days=90 per asset (3 retries)
  │     ├── fetch_market_chart_for_asset() # GET /coins/{id}/market_chart?days=91 per asset (3 retries)
  │     └── upsert_ohlcv()                 # → inotives_tradings.asset_metrics_1d
  ├── run_indicators_daily()               # common/data/indicators.py
  │     ├── load_ohlcv()                   # most recent 400 days from asset_metrics_1d
  │     ├── compute_indicators()           # pandas-ta: ATR, SMA, EMA(12/26/50/200), MACD, RSI,
  │     │                                  #            BB, Volume, ADX(14), EMA slope 5d, vol ratio
  │     └── upsert_indicators(target_dates=[today])  # → inotives_tradings.asset_indicators_1d
  └── run_regime_daily()                   # common/data/market_regime.py
        ├── load_regime_inputs()           # adx_14, ema_slope_5d, vol_ratio_14 from indicators
        ├── compute_regime_scores()        # normalise → score_adx/slope/vol → final_regime_score
        └── upsert_regime_scores()         # → inotives_tradings.asset_market_regime
```

### Running the pipeline
```bash
make daily-data                     # Run for yesterday (default)
make daily-data date=2026-03-13     # Run for specific date
```

### Cron setup (02:00 UTC daily)
```bash
0 2 * * * cd /path/to/inotives && uv run --env-file configs/envs/.env.local python bots/data_bot/main.py
```

### Data module design
Each module in `common/data/` has:
- **Pure functions** for computation (no DB dependency)
- **Async functions** for DB load/upsert (use `get_conn()` from pool)
- **`run_*()` entry points** that handle pool lifecycle (init if not already initialized, close on exit if they initialized it)
- **`run_*_backfill()` variants** for one-time historical backfill

---

## Exchange Connection Layer

Located in `common/connections/`.

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

### Architecture: Hybrid Grid + Regime-Switching

The trader bot runs two co-operating strategies. A **Regime Score (RS 0–100)** computed
daily by the pipeline determines how capital is split between them:

| RS Range | Market State | Grid capital | Trend capital |
|---|---|---|---|
| 0–30 | Deep Sideways | 100% | 0% |
| 31–60 | Hybrid/Transition | sliding scale | sliding scale |
| 61–100 | Strong Trend | 0% | 100% |

Formula: `RS = (score_adx × 0.4) + (score_slope × 0.4) + (score_vol × 0.2)`

**Capital scaling is applied at cycle-open time only** — existing open cycles always
finish with the capital they started with.

**`bots/trader_bot/hybrid_coordinator.py`** is the shared coordination layer:
- `get_regime_score_with_circuit_breaker()` — fetches RS; returns 0.0 if intraday price
  deviation > 2×ATR from daily open (circuit breaker)
- `grid_capital_limit(capital, rs)` = `capital × (100-rs)/100`
- `trend_capital_limit(capital, rs)` = `capital × rs/100`
- `trend_has_priority(conn, asset_id, rs)` — True when RS > 50 and a TREND_FOLLOW cycle is OPEN
- `grid_has_active_cycle(conn, asset_id)` — True when a DCA_GRID cycle is OPEN

**Strategy registry** (`bots/trader_bot/strategies/__init__.py`): `DCA_GRID` and `TREND_FOLLOW`

### Strategy: Volatility-Adaptive DCA Grid

Implemented in `bots/trader_bot/strategies/dca_grid.py`. The bot loads all `ACTIVE` strategies from `inotives_tradings.trade_strategies` and runs them concurrently in a 60-second polling loop.

**Fee sync at startup:**
`sync_strategy_fees()` in `bots/trader_bot/main.py` calls `exchange.fetch_trading_fees(symbol)` for each unique symbol, updates `inotives_tradings.trade_strategies` (column + JSONB metadata) if the values have changed, and updates the in-memory strategy dict for the current tick. Falls back to DB values if sync fails.

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

**Hybrid coordination hooks (added in Phase 4):**
- Checks `hybrid_coordinator.get_regime_score_with_circuit_breaker()` before opening any cycle
- RS >= 61 → grid paused; RS > 50 + TREND_FOLLOW cycle open → defers entry
- `capital_per_cycle` scaled by `(100 - RS) / 100` at cycle-open time

### Strategy: Trend Following (Momentum)

Implemented in `bots/trader_bot/strategies/trend_following.py`. `strategy_type = "TREND_FOLLOW"`.

**Entry conditions (ALL must pass):**
1. Regime Score >= `min_regime_score` (default 61) from `asset_market_regime`
2. EMA50 > EMA200 (golden cross — sustained uptrend structure)
3. Current price > 5-day high (breakout confirmation — momentum trigger)
4. ADX(14) >= `min_adx` (default 25) — trend has enough strength
5. RSI(14) < `rsi_entry_max` (default 70) — not overbought at entry
6. ATR% < `max_atr_pct_entry` (default 6%) — not in extreme volatility

**Position sizing (ATR-scaled):**
`capital_at_risk = capital_allocated × risk_pct_per_trade`
`position_size = capital_at_risk / (ATR × atr_stop_multiplier)`
Capped at `capital_allocated / current_price`. Fee-adjusted via taker fee.

**Cycle state (stored in `trade_cycles.metadata`):**
`entry_price`, `position_size`, `atr_at_entry`, `initial_stop_loss`,
`highest_price_since_entry`, `high_5d_at_entry`, `entry_order_id`

**Exit logic (trailing stop):**
- Initial stop: `entry_price - (atr_stop_multiplier × ATR)` (default 2×)
- Trailing stop: `highest_price_since_entry - (atr_trail_multiplier × ATR)` (default 3×)
- Effective stop: `MAX(initial_stop, trailing_stop)` — stop only moves up
- Trigger: `current_price <= effective_stop`

**Hybrid coordination hooks:**
- RS <= 50 + active DCA_GRID cycle → defers entry (grid has priority)
- `capital_allocated` scaled by `RS / 100` at cycle-open time
- Circuit breaker: if price deviates > 2×ATR from daily open → RS overridden to 0

**Expected strategy metadata:**
```json
{
    "capital_allocated":    1000,
    "risk_pct_per_trade":   1.0,
    "atr_stop_multiplier":  2.0,
    "atr_trail_multiplier": 3.0,
    "min_adx":              25.0,
    "min_regime_score":     61.0,
    "rsi_entry_max":        70.0,
    "max_atr_pct_entry":    6.0,
    "reserve_capital_pct":  20
}
```

---

## Reference Data Setup

### Bootstrap (fresh environment)

```bash
make bootstrap   # seeds data sources + syncs CG + allow-lists ETH/SOL networks + BTC/ETH/SOL assets
```

Internally runs in sequence:
1. `make seed-data-sources` — seeds `inotives_tradings.data_sources` from CSV
2. `make sync-coingecko-platforms` — populates `coingecko.raw_platforms` (~440 platforms)
3. `make sync-coingecko-coins` — populates `coingecko.raw_coins` (~18k coins)
4. `make allowlist-network` × 2 — ethereum, solana (Bitcoin has no CoinGecko platform entry)
5. `make allowlist-asset` × 3 — bitcoin, ethereum, solana

> ETH/SOL networks + BTC/ETH/SOL assets are the default starting point. Edit the `bootstrap` target in Makefile or run `allowlist-*` individually to customise.

### Adding assets / networks after bootstrap

```bash
make allowlist-asset   coingecko_id=dogecoin cmc_id=74
make allowlist-network coingecko_id=binance-smart-chain code=BSC
```

`coingecko.raw_coins` and `coingecko.raw_platforms` must be populated first (either via `make bootstrap` or `make sync-coingecko-coins`/`make sync-coingecko-platforms`).

### Historical OHLCV backfill

```bash
make seed-metrics-1d csv=db/seeds/btc_historical_data_20100513-20260309.csv

uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['btc']))"
```

---

## Setup Guide (Clone to Trading-Ready)

Complete step-by-step instructions to set up this project from a fresh clone. Follow every step in order.

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (v0.5+)
- Docker (only needed if running standalone PostgreSQL)

### Step 1 — Clone and install dependencies

```bash
git clone git@github-personal:inotives/inotives_cryptos.git inotives
cd inotives
make init
```

### Step 2 — Configure environment

```bash
cp configs/envs/.env.example configs/envs/.env.local
```

Edit `configs/envs/.env.local` — set at minimum:

```env
DB_CONTAINER_NAME=inotives_postgres   # Docker container name
DB_HOST=localhost
DB_PORT=5445
DB_USER=inotives
DB_PASSWORD=<your_password>
DB_NAME=inotives                      # Database name (schemas live inside this)
COINGECKO_API_KEY=<your_key>          # Free demo key from https://www.coingecko.com/en/api/pricing
COINGECKO_API_KEY_TYPE=demo           # "demo" or "pro"
```

Exchange keys (`CRYPTOCOM_API_KEY`, `BINANCE_API_KEY`) are optional — only needed for live trading.

### Step 3 — Ensure database is running

```bash
make db-ensure
```

This auto-detects the DB server and creates the database:
1. Checks if `DB_HOST:DB_PORT` is reachable → if not, starts local Docker PostgreSQL via `make db-up`
2. Creates the `DB_NAME` database on the server if it doesn't exist via `make db-create`

### Step 4 — Run database migrations

```bash
make migrate-up
```

Applies all 27 migrations — creates `inotives_tradings` and `coingecko` schemas with all tables, triggers, and functions.

Verify: `make migrate-status` (all should show `[X]`).

### Step 5 — Bootstrap reference data

```bash
make bootstrap
```

Runs 5 steps in sequence:
1. Seeds `inotives_tradings.data_sources` (6 sources: CoinGecko, CMC, 4 exchanges)
2. Syncs `coingecko.raw_platforms` from CoinGecko API (~440 platforms)
3. Syncs `coingecko.raw_coins` from CoinGecko API (~18k coins)
4. Allow-lists default networks: Ethereum, Solana (Bitcoin has no CoinGecko platform entry)
5. Allow-lists default assets: BTC, ETH, SOL

After bootstrap, add more assets with:
```bash
python -m common.tools.manage_assets search-coins --query dogecoin   # find coingecko_id
make allowlist-asset coingecko_id=dogecoin                           # add to allow-list
```

### Step 6 — Seed historical data (CRITICAL)

**The trading bot will NOT work without historical data.** Technical indicators require a minimum of **220 days** of daily OHLCV data per asset. Without it, the indicator pipeline skips the asset entirely — no indicators, no regime score, no trading signals.

CoinGecko free tier only provides ~90 days, which is insufficient. You must backfill from CoinMarketCap CSV exports.

**6a. Download CSVs from CoinMarketCap:**
- BTC: https://coinmarketcap.com/currencies/bitcoin/historical-data/
- ETH: https://coinmarketcap.com/currencies/ethereum/historical-data/
- SOL: https://coinmarketcap.com/currencies/solana/historical-data/
- Save to `db/seeds/` with naming: `{asset_code}_historical_data_{YYYYMMDD}.csv`

**6b. Import OHLCV and backfill indicators for each asset:**

```bash
# Import OHLCV from CSV
make seed-metrics-1d csv=db/seeds/btc_historical_data_20260315.csv
make seed-metrics-1d csv=db/seeds/eth_historical_data_20260315.csv
make seed-metrics-1d csv=db/seeds/sol_historical_data_20260315.csv

# Backfill technical indicators (per asset)
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['btc']))"
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['eth']))"
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['sol']))"

# Backfill regime scores (all assets at once)
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.market_regime import run_regime_backfill; asyncio.run(run_regime_backfill())"
```

**Recommendation:** Download at least 1–2 years of historical data per asset.

### Step 7 — Run daily data pipeline

```bash
make daily-data                     # Fetches yesterday's OHLCV → indicators → regime
make daily-data date=2026-03-14     # Specific date
```

### Step 8 — Start the bots

```bash
# Terminal 1 — pricing bot (polls live tickers every 60s)
make pricing-bot

# Terminal 2 — trader bot (runs strategies in 60s polling loop)
make trader-bot market=btc/usdt
```

The pricing bot must be running for the trader bot's intraday safeguards (circuit breaker, volatility guard) to work.

### Step 9 — Schedule cron jobs

```bash
python -m common.tools.manage_cron install          # Install all cron jobs
python -m common.tools.manage_cron list              # Verify
```

Jobs installed:
- `daily-data` — 02:00 UTC daily: OHLCV → indicators → regime scores
- `coingecko-sync` — 01:00 UTC Sunday: refresh platforms + coins list

### Step 10 — Run tests (verification)

```bash
uv run pytest tests/ -q
```

All 101 tests should pass.

---

## Makefile Commands

```bash
# Database
make db-ensure                      # Auto-detect DB server + create database if needed
make db-up                          # Start local Docker PostgreSQL
make db-down                        # Stop local Docker PostgreSQL
make db-create                      # Create database on server (if not exists)
make db-status                      # Check if database server is reachable

# Migrations
make migrate-up                     # Apply all pending migrations
make migrate-down                   # Roll back last migration
make migrate-status                 # Show migration state
make migrate-new name=<name>        # Create new migration file

# Bootstrap
make bootstrap                      # Full initial setup (seed + sync CG + allow-list defaults)
make sync-coingecko-platforms       # Manually run platform sync
make sync-coingecko-coins           # Manually run coin list sync

# Daily Data Pipeline
make daily-data                     # Run OHLCV → indicators → regime (default: yesterday)
make daily-data date=2026-03-13     # Run for specific date

# Allow-listing
make allowlist-asset coingecko_id=<id>              # Promote coin to inotives_tradings.assets
make allowlist-asset coingecko_id=<id> cmc_id=<id>  # With CMC mapping
make allowlist-asset-dry coingecko_id=<id>          # Preview only
make allowlist-network coingecko_id=<id>            # Promote platform to inotives_tradings.networks
make allowlist-network-dry coingecko_id=<id>        # Preview only

# Seeding
make seed-data-sources              # Seed inotives_tradings.data_sources from CSV (required once)
make seed-metrics-1d csv=<path>     # Import daily OHLCV from CMC CSV export

# Bots
make pricing-bot                    # Start the pricing bot
make trader-bot                     # Start the trader bot
make setup-paper-trading            # Create paper trading venue + strategies in DB
make manage-trading                 # Interactive CLI for strategy/cycle CRUD

# Init
make init                           # Create venv + install all deps (fresh clone)
```

---

## Config Fields (pydantic Settings)

Defined in `common/config.py`:

| Field | Env var | Notes |
|---|---|---|
| `db_host` | `DB_HOST` | `localhost` (standalone or shared) |
| `db_port` | `DB_PORT` | `5445` (default mapped port) |
| `db_user` | `DB_USER` | `inotives` |
| `db_password` | `DB_PASSWORD` | Note: field is `db_password`, NOT `db_pass` |
| `db_name` | `DB_NAME` | `inotives` (standalone) or `inotives_aibots` (shared) |
| `cryptocom_api_key` | `CRYPTOCOM_API_KEY` | Optional — public endpoints work without it |
| `cryptocom_api_secret` | `CRYPTOCOM_API_SECRET` | |
| `binance_api_key` | `BINANCE_API_KEY` | |
| `binance_api_secret` | `BINANCE_API_SECRET` | |
| `coingecko_api_key` | `COINGECKO_API_KEY` | Optional — free tier works |
| `coingecko_api_key_type` | `COINGECKO_API_KEY_TYPE` | `"demo"` (default) or `"pro"` — controls which auth header is used |

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
- **Merged**: `feat-initial-db-migration` (all 17 migrations), `INO-0001/seeding-the-tables`, `INO-0002/pricing-bots`, `INO-0003/restructure-compact-trading-bot`

---

## Known Gotchas

- **`db_password` not `db_pass`** — pydantic field was renamed to match `DB_PASSWORD` env var. Do not revert.
- **Shared DB mode** — when sharing the `inotives_aibots` database with the aibots project, this project uses `inotives_tradings` and `coingecko` schemas. Do not modify schemas owned by other projects. In standalone mode, the entire database is ours.
- **CoinGecko OHLCV granularity** — free tier `/ohlc?days=N` returns daily candles only when `days >= 90`. Module uses `days=90` and filters to the target date.
- **CoinGecko market chart granularity** — free/demo tier requires `days > 90` for daily granularity. Module uses `days=91`. Pro tier sends `interval=daily` explicitly.
- **CoinGecko API key headers** — Demo key uses `x-cg-demo-api-key`. Pro key uses `x-cg-pro-api-key`. Using the wrong header silently ignores the key. Controlled by `COINGECKO_API_KEY_TYPE` env var.
- **`allowlist_asset` / `allowlist_network` require raw tables populated first** — `coingecko.raw_coins` and `coingecko.raw_platforms` must be synced before running allowlist scripts. `make bootstrap` handles this automatically.
- **Crypto.com `fetch_tickers`** — public endpoint doesn't accept a symbol list. `CryptoComConnection.fetch_tickers()` fetches all tickers and filters client-side.
- **`volume_24h` on Crypto.com** — public ticker doesn't return `quoteVolume`. `CcxtRestConnection._normalise_ticker()` approximates it as `baseVolume * last`.
- **Crypto.com fees** — actual rates: 0.25% maker (`0.0025`) / 0.50% taker (`0.005`). Bot syncs live fees at startup via `fetch_trading_fees()`.
- **`metadata` JSONB from asyncpg** — asyncpg returns JSONB columns as raw strings. Always wrap with `json.loads()` when `isinstance(value, str)`. See `bots/trader_bot/main.py:load_active_strategies()`.
- **`asset_metrics_1d` volume column** — column is named `volume_usd`, not `volume`. Any raw SQL must use `volume_usd`.
- **`price_observations` composite PK** — uses `(id, observed_at)` composite PK. Regular table (not a TimescaleDB hypertable).
