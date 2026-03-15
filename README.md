# Inotives Tradings

A personal automated crypto trading system — from raw data ingestion to live strategy execution. Pure Python asyncio bots with cron-scheduled data pipelines, backed by a shared PostgreSQL instance.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          Data Sources                           │
│              CoinGecko · Crypto.com · Binance (ccxt)            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              common/data/  (cron-scheduled or manual)            │
│  coingecko_sync.py → coingecko.raw_platforms + raw_coins        │
│  ohlcv.py          → inotives_tradings.asset_metrics_1d         │
│  indicators.py     → inotives_tradings.asset_indicators_1d      │
│  market_regime.py  → inotives_tradings.asset_market_regime      │
│                                                                 │
│  bots/data_bot/main.py  (02:00 UTC cron)                    │
│    OHLCV → indicators → regime scores                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│          PostgreSQL (shared with inotives_aibots project)        │
│  coingecko schema: raw_coins · raw_platforms  (CG universe)     │
│  inotives_tradings schema:                                      │
│    networks · assets · asset_source_mappings                    │
│    asset_metrics_1d · asset_indicators_1d · asset_market_regime │
│    price_observations · trade_* · capital_locks                 │
└──────────┬──────────────────────────────────────┬───────────────┘
           │                                      │
           ▼                                      ▼
┌─────────────────────┐           ┌───────────────────────────────┐
│  pricing_bot        │           │  trader_bot                   │
│  polls tickers →    │           │  DCA Grid + Trend Following   │
│  price_observations │           │  Hybrid regime-switching      │
└─────────────────────┘           └───────────────────────────────┘
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Package manager | `uv` |
| Database | PostgreSQL (shared, pgvector image, port 5445) |
| Migrations | `dbmate` (via `uvx`) |
| Exchange API | `ccxt` (Crypto.com, Binance, ...) |
| Market data | CoinGecko REST API |
| Indicator maths | `pandas` + `pandas-ta` |
| Config | `pydantic-settings` (.env files) |
| Task automation | `Makefile` + cron |

---

## Project Structure

```
inotives/
├── common/
│   ├── config.py               # pydantic Settings — reads from .env.local
│   ├── db.py                   # asyncpg connection pool (init_pool / get_conn)
│   ├── connections/            # Exchange connection layer
│   │   ├── base.py             # Abstract BaseExchangeConnection + TypedDicts
│   │   ├── ccxt_rest.py        # Generic ccxt REST + fetch_trading_fees()
│   │   ├── paper.py            # PaperTradingConnection (simulated fills)
│   │   ├── __init__.py         # get_exchange() factory
│   │   └── exchanges/
│   │       └── cryptocom.py    # Crypto.com override
│   ├── api/
│   │   └── coingecko.py        # CoinGecko REST client (pure requests)
│   ├── data/
│   │   ├── ohlcv.py            # Daily OHLCV fetch + upsert
│   │   ├── indicators.py       # Technical indicator computation
│   │   ├── market_regime.py    # Regime score computation
│   │   └── coingecko_sync.py   # CoinGecko reference sync (platforms + coins)
│   └── tools/
│       ├── manage_assets.py    # Asset/network/source management CLI
│       ├── manage_trading.py   # Strategy/cycle management CLI
│       ├── manage_cron.py      # Cron job manager (install/remove/list)
│       ├── allowlist_asset.py  # Promote CoinGecko coin → internal allow-list
│       ├── allowlist_network.py # Promote CoinGecko platform → internal allow-list
│       └── setup_paper_trading.py  # Create paper trading venue + strategies
├── bots/
│   ├── data_bot/
│   │   └── main.py             # Daily pipeline: OHLCV → indicators → regime
│   ├── pricing_bot/
│   │   └── main.py             # CLI: --exchange-id --source-code --pair
│   └── trader_bot/
│       ├── main.py             # Bot entry point — fee sync + polling loop
│       ├── hybrid_coordinator.py   # Regime-based capital allocation + circuit breaker
│       └── strategies/
│           ├── base.py         # Abstract base strategy
│           ├── dca_grid.py     # DcaGridStrategy (volatility-adaptive)
│           └── trend_following.py  # TrendFollowingStrategy (momentum)
├── tests/                      # pytest suite
│   ├── connections/            # Exchange connection tests
│   ├── pricing_bot/            # Pricing bot tests
│   └── strategies/             # Strategy unit tests (DCA Grid + Trend Following)
├── configs/envs/
│   ├── .env.example            # Template (committed)
│   └── .env.local              # Local secrets (gitignored — never commit)
├── db/
│   ├── migrations/             # dbmate SQL migration files (27 migrations)
│   ├── scripts/                # Seeding scripts (data sources, metrics, assets, networks)
│   └── seeds/                  # CSV seed data (historical OHLCV, source mappings)
├── Makefile
├── pyproject.toml
└── pytest.ini
```

---

## Getting Started

### Prerequisites

1. **PostgreSQL** — this project connects to the shared `inotives_aibots` database (managed by the aibots project's Docker Compose on port 5445). The database must be running before you proceed.
2. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — Python package manager (v0.5+)
3. **Python 3.12+**

---

### Step 1 — Clone and install dependencies

```bash
git clone git@github-personal:inotives/inotives_cryptos.git inotives
cd inotives
make init
```

This creates a virtual environment and installs all dependencies via `uv`.

### Step 2 — Configure environment

```bash
cp configs/envs/.env.example configs/envs/.env.local
```

Edit `configs/envs/.env.local` with your actual values:

```env
# Required — database connection
DB_HOST=localhost
DB_PORT=5445
DB_USER=inotives
DB_PASSWORD=your_actual_password
DB_NAME=inotives_aibots

# CoinGecko API key (free demo key works)
COINGECKO_API_KEY=your_key
COINGECKO_API_KEY_TYPE=demo

# Exchange API keys (needed for live trading)
CRYPTOCOM_API_KEY=your_key
CRYPTOCOM_API_SECRET=your_secret
```

**Where to get API keys:**

| Key | Where to get it | Notes |
|---|---|---|
| CoinGecko | https://www.coingecko.com/en/api/pricing → sign up for free Demo plan | Free tier has rate limits but works for daily pipeline. Set `COINGECKO_API_KEY_TYPE=demo`. For paid plans, set to `pro`. |
| Crypto.com | https://exchange.crypto.com/ → Settings → API Keys → Create API Key | Required for live trading. Public endpoints (ticker polling) work without keys. |
| Binance | https://www.binance.com/en/my/settings/api-management | Only needed if using Binance as an exchange. |
| CoinMarketCap | https://coinmarketcap.com/api/ → sign up for free Basic plan | Only needed if seeding historical OHLCV from CMC CSV exports. |

The database connection must be configured correctly before proceeding. Exchange keys can be added later when you're ready to trade.

### Step 3 — Run database migrations

```bash
make migrate-up
```

Creates the `inotives_tradings` and `coingecko` schemas with all 27 migrations (tables, triggers, functions).

Verify:

```bash
make migrate-status
```

### Step 4 — Bootstrap reference data

```bash
make bootstrap
```

This runs a 5-step sequence:

| Step | What it does |
|---|---|
| 1 | Seeds `inotives_tradings.data_sources` (CoinGecko, CMC, exchanges) |
| 2 | Syncs `coingecko.raw_platforms` from CoinGecko API (~150 platforms) |
| 3 | Syncs `coingecko.raw_coins` from CoinGecko API (~13k coins) |
| 4 | Allow-lists default networks: Ethereum, Bitcoin, Solana |
| 5 | Allow-lists default assets: BTC, ETH, SOL |

After bootstrap, only BTC/ETH/SOL are in the internal allow-list. To add more assets, first search the synced CoinGecko universe to find the correct `coingecko_id`:

```bash
# Search for coins by name, symbol, or ID
python -m common.tools.manage_assets search-coins --query dogecoin
python -m common.tools.manage_assets search-coins --query ada

# Search returns: coingecko_id, symbol, name — use the coingecko_id to allow-list
```

Then allow-list the asset and (optionally) its network:

```bash
# Allow-list an asset (coingecko_id is required, cmc_id is optional)
python -m common.tools.manage_assets add-asset --coingecko-id dogecoin
python -m common.tools.manage_assets add-asset --coingecko-id cardano --cmc-id 2010

# Preview before committing (dry-run)
python -m common.tools.manage_assets add-asset --coingecko-id dogecoin --dry-run

# Allow-list a network
python -m common.tools.manage_assets add-network --coingecko-id avalanche

# Or via Makefile shortcuts
make allowlist-asset coingecko_id=dogecoin cmc_id=74
make allowlist-network coingecko_id=binance-smart-chain code=BSC
```

To see what's currently allow-listed:

```bash
python -m common.tools.manage_assets list-assets
python -m common.tools.manage_assets list-networks
```

### Step 5 — Seed historical data

> **This step is critical.** The trading bot relies on technical indicators that require substantial historical data to compute correctly. Without backfilled data, the system **will not work properly**.

**Why backfilling matters:**

The indicator pipeline requires a minimum of **220 days** of daily OHLCV data per asset. If an asset has fewer than 220 days, the pipeline **skips it entirely** — no indicators, no regime score, no trading.

Even CoinGecko's free-tier OHLCV endpoint only returns ~90 days of daily data. Running `make daily-data` alone gives you ~90 days at best, which means:

| Indicator | Days needed | With only 90 days |
|---|---|---|
| SMA(200) | 200 | Not computed — NULL |
| EMA(200) | 200 | Not computed — NULL |
| ADX(14) | ~160 (needs EMA convergence) | Unreliable |
| EMA slope 5d% | 205 (EMA200 + 5 days) | Not computed — NULL |
| Regime Score | Depends on all above | Wrong or missing |

**The consequences of insufficient data:**
- **Entry conditions fail silently** — golden cross check (`EMA50 > EMA200`) can't run if EMA200 is NULL, so the bot never enters
- **Regime Score is wrong** — if EMA slope or vol ratio is NULL, the RS formula produces a misleading score (e.g. RS = 31.7 when it should be 65), causing the wrong strategy to get capital
- **Grid spacing is off** — ATR(14) converges quickly but ATR% regime classification (low/normal/high) depends on `atr_sma_20` which needs 34+ days to stabilise
- **The bot sits idle** — with missing indicators, most entry conditions fail, and the bot does nothing

**How to backfill:**

1. Download historical data from CoinMarketCap (requires free account):
   - BTC: https://coinmarketcap.com/currencies/bitcoin/historical-data/
   - ETH: https://coinmarketcap.com/currencies/ethereum/historical-data/
   - SOL: https://coinmarketcap.com/currencies/solana/historical-data/
   - Click **Download CSV** and save to `db/seeds/`

2. Rename the downloaded file — the asset code must be the first segment before `_`:
   ```
   {asset_code}_historical_data_{YYYYMMDD}.csv
   ```
   Examples:
   ```
   btc_historical_data_20260315.csv
   eth_historical_data_20260315.csv
   sol_historical_data_20260315.csv
   ```

3. Seed the data and backfill indicators:
   ```bash
   # Import OHLCV from the CSV
   make seed-metrics-1d csv=db/seeds/btc_historical_data_20260315.csv

   # Backfill technical indicators for the imported data
   uv run --env-file configs/envs/.env.local python -c \
       "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['btc']))"
   ```

   Repeat for each asset you want to trade.

4. After backfilling, run the regime score computation:
   ```bash
   uv run --env-file configs/envs/.env.local python -c \
       "import asyncio; from common.data.market_regime import run_regime_backfill; asyncio.run(run_regime_backfill())"
   ```

> **Recommendation:** Download at least 1–2 years of historical data for each asset you plan to trade. This ensures all indicators (including SMA200 and EMA200) have fully converged and the regime score is accurate from day one.

### Step 6 — Run the daily data pipeline

Once historical data is seeded, keep it up to date with the daily pipeline:

```bash
make daily-data                     # Fetches yesterday's data (default)
make daily-data date=2026-03-14     # Specific date
```

This runs: OHLCV fetch (CoinGecko) → technical indicators → market regime scores. Schedule this via cron (see Step 8) so it runs automatically.

### Step 7 — Start the bots

Run each bot in a separate terminal:

```bash
# Terminal 1 — pricing bot (polls live tickers every 60s)
make pricing-bot

# Terminal 2 — trader bot (runs active strategies in a 60s polling loop)
make trader-bot
```

The pricing bot feeds `price_observations`, which the trader bot uses for live decision-making.

### Step 8 — Schedule cron jobs

Install the daily data pipeline and weekly CoinGecko sync as cron jobs:

```bash
# Preview what will be installed
python -m common.tools.manage_cron install --dry-run

# Install all cron jobs
python -m common.tools.manage_cron install

# Or install individually
python -m common.tools.manage_cron install daily-data       # 02:00 UTC daily
python -m common.tools.manage_cron install coingecko-sync   # 01:00 UTC weekly (Sunday)

# Verify
python -m common.tools.manage_cron list
```

Or via Makefile:

```bash
make cron-install job=daily-data
make cron-list
```

---

## Management CLIs

All management scripts support dual-mode operation: **interactive** (TUI menu) and **non-interactive** (subcommands with `--json` output for automation).

### Trading Management

```bash
# Interactive mode (TUI menu)
make manage-trading

# Non-interactive subcommands
python -m common.tools.manage_trading list-strategies
python -m common.tools.manage_trading list-strategies --json
python -m common.tools.manage_trading view-strategy --strategy-id 5
python -m common.tools.manage_trading activate --strategy-id 5
python -m common.tools.manage_trading pause --strategy-id 5
python -m common.tools.manage_trading update --strategy-id 5 --param risk_pct_per_trade=1.5 --param min_adx=30
python -m common.tools.manage_trading list-cycles --strategy-id 5
python -m common.tools.manage_trading close-cycle --cycle-id 10
```

### Asset & Network Management

```bash
# Interactive mode (TUI menu)
make manage-assets

# Non-interactive subcommands
python -m common.tools.manage_assets list-assets
python -m common.tools.manage_assets list-assets --json
python -m common.tools.manage_assets view-asset --code btc
python -m common.tools.manage_assets search-coins --query dog
python -m common.tools.manage_assets add-asset --coingecko-id dogecoin --dry-run
python -m common.tools.manage_assets add-asset --coingecko-id dogecoin --cmc-id 74
python -m common.tools.manage_assets remove-asset --asset-id 5
python -m common.tools.manage_assets list-networks
python -m common.tools.manage_assets add-network --coingecko-id avalanche
python -m common.tools.manage_assets list-sources
python -m common.tools.manage_assets list-mappings --asset-id 1
python -m common.tools.manage_assets pricing-pairs
python -m common.tools.manage_assets pricing-pairs --json
```

### Cron Management

```bash
python -m common.tools.manage_cron list                  # Show active cron jobs
python -m common.tools.manage_cron install               # Install all jobs
python -m common.tools.manage_cron install daily-data     # Install specific job
python -m common.tools.manage_cron remove                 # Remove all inotives jobs
python -m common.tools.manage_cron remove coingecko-sync  # Remove specific job
```

---

## Makefile Reference

### Setup & Migrations

```bash
make init                             # Create venv + install dependencies
make migrate-up                       # Apply all pending migrations
make migrate-down                     # Roll back the last migration
make migrate-status                   # Show migration status
make migrate-new name=<name>          # Create a new migration file
```

### Bootstrap & Reference Data

```bash
make bootstrap                        # Full initial setup (seed + sync + allow-list defaults)
make sync-coingecko-platforms         # Manually sync CoinGecko platforms
make sync-coingecko-coins             # Manually sync CoinGecko coin list
make seed-data-sources                # Seed data sources from CSV
```

### Asset Allow-listing

```bash
make allowlist-asset coingecko_id=bitcoin
make allowlist-asset coingecko_id=ethereum cmc_id=1027
make allowlist-asset-dry coingecko_id=bitcoin           # Preview only
make allowlist-network coingecko_id=ethereum
make allowlist-network-dry coingecko_id=ethereum         # Preview only
```

### Daily Data Pipeline

```bash
make daily-data                       # Run OHLCV → indicators → regime (yesterday)
make daily-data date=2026-03-13       # Run for specific date
```

### Seeding

```bash
make seed-metrics-1d csv=<path>       # Import daily OHLCV from CMC CSV
make seed-metrics-1d-dry csv=<path>   # Preview only
```

### Bots

```bash
make pricing-bot                      # Start pricing bot (default: Crypto.com, BTC/ETH/SOL/CRO)
make pricing-bot exchange=binance pairs="btc/usdt eth/usdt"
make trader-bot                       # Start trader bot (default: BTC/USDT)
make trader-bot market=sol/usdt
```

### Management

```bash
make manage-trading                   # Trading strategy/cycle CLI (interactive)
make manage-assets                    # Asset/network management CLI (interactive)
make manage-assets cmd="list-assets --json"   # Non-interactive via Makefile
make cron-list                        # Show active cron jobs
make cron-install job=daily-data      # Install cron job
make cron-remove job=daily-data       # Remove cron job
```

### Testing

```bash
uv run pytest tests/ -q              # Run all tests
uv run pytest tests/strategies/ -q   # Run strategy tests only
```

---

## Database Schema

### Schema layout

| Schema | Purpose |
|---|---|
| `coingecko` | Raw data from CoinGecko API — full universe, no curation |
| `inotives_tradings` | Internal curated data — trading allow-list, metrics, indicators, trading state |

### Allow-list model

`coingecko.raw_coins` and `coingecko.raw_platforms` hold the full CoinGecko universe (~13k coins, ~150 platforms). `inotives_tradings.assets` and `inotives_tradings.networks` are the **internal allow-lists** — only assets explicitly promoted via `make allowlist-asset` (or `python -m common.tools.manage_assets add-asset`) are visible to data modules and bots.

### Key tables

| Table | Purpose |
|---|---|
| `coingecko.raw_coins` | Full CoinGecko coin list. Synced weekly. |
| `coingecko.raw_platforms` | Full CoinGecko platform list. Synced weekly. |
| `inotives_tradings.assets` | Allow-listed assets for trading/tracking |
| `inotives_tradings.networks` | Allow-listed blockchain networks |
| `inotives_tradings.data_sources` | External data source registry |
| `inotives_tradings.asset_source_mappings` | Maps internal asset_id → external identifiers |
| `inotives_tradings.asset_metrics_1d` | Daily OHLCV + market cap + supply |
| `inotives_tradings.asset_indicators_1d` | Pre-computed daily indicators (ATR, RSI, EMA, etc.) |
| `inotives_tradings.asset_market_regime` | Daily regime scores per asset (0–100) |
| `inotives_tradings.price_observations` | Live ticker snapshots from pricing bot |
| `inotives_tradings.trade_strategies` | Strategy config (type, parameters, fees) |
| `inotives_tradings.trade_cycles` | Active/closed trading cycles |
| `inotives_tradings.trade_grid_levels` | DCA Grid level rows per cycle |
| `inotives_tradings.trade_orders` | All placed orders |
| `inotives_tradings.capital_locks` | Capital reserved per active cycle |

### Mutable table conventions

Every mutable table has:
- **Audit fields** — `created_at`, `updated_at`, `created_by`, `updated_by`
- **Soft delete** — `deleted_at`, `deleted_by` (DELETE intercepted by trigger)
- **Versioning** — `version` counter + `sys_period` temporal range
- **History table** — `<table>_history` with field-level diffs

Append-only tables (`asset_metrics_1d`, `price_observations`, `trade_executions`, etc.) have no soft delete or versioning.

---

## Daily Data Pipeline

The pipeline runs as `bots/data_bot/main.py`, scheduled via cron at 02:00 UTC daily.

```
data_bot.main(target_date)
  ├── run_ohlcv_fetch()       → inotives_tradings.asset_metrics_1d
  ├── run_indicators_daily()  → inotives_tradings.asset_indicators_1d
  └── run_regime_daily()      → inotives_tradings.asset_market_regime
```

### Technical Indicators Computed

| Category | Indicators |
|---|---|
| Volatility | ATR(14), ATR(20), ATR%, ATR SMA(20), volatility regime |
| Trend — MA | SMA(20), SMA(50), SMA(200), EMA(12), EMA(26), EMA(50), EMA(200) |
| Trend — MACD | MACD line, signal(9), histogram |
| Momentum | RSI(14), ADX(14) |
| Bands | Bollinger Bands(20, 2σ) — upper, middle, lower, width % |
| Volume | Volume SMA(20), volume ratio |
| Regime | EMA slope 5d%, vol ratio 14d |

### Market Regime Score

`RS = (score_adx × 0.4) + (score_slope × 0.4) + (score_vol × 0.2)`

Each component is normalised to 0–100. The final RS determines capital allocation between DCA Grid and Trend Following strategies.

---

## Exchange Connection Layer

Located in `common/connections/`.

```
BaseExchangeConnection  (abstract)
    └── CcxtRestConnection     (generic ccxt REST)
            ├── CryptoComConnection  (Crypto.com override)
            └── PaperTradingConnection  (simulated fills)
```

```python
from common.connections import get_exchange
exchange = get_exchange("cryptocom")                           # public, no key
exchange = get_exchange("cryptocom", api_key=..., secret=...)  # authenticated
exchange = get_exchange("binance", api_key=..., secret=...)    # any ccxt exchange
```

---

## Trader Bot

### Hybrid Grid + Regime-Switching

The trader bot runs two co-operating strategies. A **Regime Score (RS 0–100)** computed daily determines capital allocation:

| RS Range | Market State | Grid | Trend |
|---|---|---|---|
| 0–30 | Sideways | 100% | 0% |
| 31–60 | Transition | sliding | sliding |
| 61–100 | Strong Trend | 0% | 100% |

Capital scaling is applied at cycle-open time only — existing cycles always finish with their starting capital.

### DCA Grid Strategy

- ATR-based grid spacing, regime-aware calibration (low/normal/high volatility)
- Weighted capital allocation (deeper levels = more capital)
- Defensive mode: wider grid when downtrend + intraday RSI bounce detected
- Fee-corrected quantities: `qty = capital / (price × (1 + maker_fee_pct))`
- Intraday volatility guard: pauses new fills when 4-hour price range exceeds daily ATR

### Trend Following Strategy

- Entry: EMA50 > EMA200, price > 5-day high, ADX >= 25, RS >= 61, intraday RSI < 70
- ATR-scaled position sizing with risk % per trade
- Rising trailing stop using live intraday ATR: `MAX(initial_stop, highest_price - ATR × multiplier)`
- Intraday RSI guard: blocks entry when live 1-hour RSI indicates overbought conditions

### Intraday Safeguards

Both strategies use live exchange data to compensate for the daily indicator update cycle:

| Guard | Source | Used by |
|---|---|---|
| Live ATR (1h candles) | Exchange OHLCV via ccxt | Trend Following trailing stop |
| Live RSI (1h candles) | Exchange OHLCV via ccxt | Trend Following entry guard |
| Intraday volatility | `price_observations` 4h range vs daily ATR | DCA Grid fill pause |
| Circuit breaker | `price_observations` daily open vs current price | Hybrid coordinator |

---

## Running Multiple Strategies

The architecture supports running multiple strategies in parallel for the same asset. Each strategy is an independent row in `trade_strategies` with its own config, capital locks, and cycles.

### Parallel strategies (works today, no code changes)

You can create multiple `DCA_GRID` or `TREND_FOLLOW` strategies for the same asset with different parameters. For example, a conservative and aggressive grid side by side:

| Strategy | Type | Capital | Uptrend Required | Golden Cross | Defensive RSI | Use Case |
|---|---|---|---|---|---|---|
| BTC Grid Conservative | `DCA_GRID` | $1,000 | Yes | Yes | < 40 | Bull/sideways markets |
| BTC Grid Aggressive | `DCA_GRID` | $500 | No | No | < 50 | Bear market accumulation |
| BTC Trend | `TREND_FOLLOW` | $500 | — | — | — | Strong uptrends |

All three run in the same trader bot process, share the hybrid coordinator, and have independent cycles and capital locks preventing over-allocation.

To set this up:

```bash
# Create a bear-market grid via the management CLI
python -m common.tools.manage_trading interactive

# Or swap strategies on/off as market conditions change
python -m common.tools.manage_trading pause --strategy-id 1      # pause conservative
python -m common.tools.manage_trading activate --strategy-id 7   # activate aggressive
```

### Multiple bot instances

You can also run separate trader bot processes for different markets:

```bash
# Terminal 1 — BTC strategies
make trader-bot market=btc/usdt

# Terminal 2 — SOL strategies
make trader-bot market=sol/usdt
```

They share the same database and capital locks table — no conflicts.

### Future improvement: custom strategy types

Currently the system supports two strategy types (`DCA_GRID` and `TREND_FOLLOW`) with the hybrid coordinator managing capital allocation between them. Adding a new strategy type (e.g. `BEAR_GRID`, `MEAN_REVERSION`, or a short-selling strategy) requires:

1. A new strategy class in `bots/trader_bot/strategies/` implementing the `BaseStrategy` interface
2. One-line registration in `strategies/__init__.py`
3. Update the hybrid coordinator to include the new type in priority rules and capital splitting

The trader bot's dispatch loop and all database infrastructure (cycles, orders, capital locks) work automatically for any registered strategy type.

---

## Environment Files

| File | Purpose |
|---|---|
| `configs/envs/.env.example` | Template — copy to `.env.local` |
| `configs/envs/.env.local` | Local development (gitignored — never commit) |

All `.env.*` files (except `.env.example`) are gitignored. Never commit credentials.

### Required variables

| Variable | Example | Notes |
|---|---|---|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5445` | Mapped port from aibots project |
| `DB_USER` | `inotives` | Database user |
| `DB_PASSWORD` | `your_password` | Database password |
| `DB_NAME` | `inotives_aibots` | Shared database name |

### Optional variables

| Variable | Default | Notes |
|---|---|---|
| `COINGECKO_API_KEY` | — | Free tier works without it |
| `COINGECKO_API_KEY_TYPE` | `demo` | `demo` or `pro` (controls auth header) |
| `CRYPTOCOM_API_KEY` | — | Needed for live trading (not paper mode) |
| `CRYPTOCOM_API_SECRET` | — | Needed for live trading (not paper mode) |
| `BINANCE_API_KEY` | — | If using Binance |
| `BINANCE_API_SECRET` | — | If using Binance |
