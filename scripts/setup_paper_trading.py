"""
One-time setup for paper trading — Hybrid Grid + Trend Following.

Creates (idempotent — safe to re-run):
  1. Crypto.com (Paper) venue
  2. DCA_GRID strategy  for BTC/USDT
  3. DCA_GRID strategy  for SOL/USDT
  4. TREND_FOLLOW strategy for BTC/USDT
  5. TREND_FOLLOW strategy for SOL/USDT

Capital is intentionally split so the two strategy types share the same
configured total, and the HybridCoordinator scales each at cycle-open time
based on the current regime score:

  Grid capital  = configured × (100 - RS) / 100
  Trend capital = configured × RS / 100

At RS=31.7 (current BTC state):
  Grid  gets ~68% of 1000  ≈ 683
  Trend gets ~32% of 500   ≈ 158  (and won't enter until RS ≥ 61)

Usage:
    uv run --env-file configs/envs/.env.local python scripts/setup_paper_trading.py
    uv run --env-file configs/envs/.env.local python scripts/setup_paper_trading.py --dry-run
"""

import argparse
import asyncio
import json
import os

import asyncpg


async def get_conn():
    return await asyncpg.connect(
        host     = os.environ["DB_HOST"],
        port     = int(os.environ.get("DB_PORT", 5432)),
        user     = os.environ["DB_USER"],
        password = os.environ["DB_PASSWORD"],
        database = os.environ["DB_NAME"],
    )


VENUE_NAME          = "Crypto.com (Paper)"
CRYPTOCOM_SOURCE_ID = 1     # exchange:cryptocom

# Asset IDs
BTC_ID  = 26
SOL_ID  = 120
USDT_ID = 134

# ── DCA Grid parameters ────────────────────────────────────────────────────────
# capital_per_cycle is the MAX configured allocation.
# The HybridCoordinator scales it down at cycle-open time by (100-RS)/100.
DCA_GRID_PARAMS = {
    "capital_per_cycle":          1000,
    "num_levels":                 5,
    "weights":                    [1, 1, 2, 3, 3],
    "atr_multiplier_low":         0.4,
    "atr_multiplier_normal":      0.5,
    "atr_multiplier_high":        0.7,
    "profit_target_low":          1.0,
    "profit_target_normal":       1.5,
    "profit_target_high":         2.5,
    "max_atr_pct_entry":          6.0,
    "rsi_entry_max":              60,
    "reserve_capital_pct":        30,
    "maker_fee_pct":              0.0025,
    "taker_fee_pct":              0.005,
    "circuit_breaker_atr_pct":    8.0,
    "max_expansions":             1,
    "expansion_levels":           2,
    "expansion_capital_fraction": 0.3,
    # Entry filters — let regime + indicators govern naturally
    "require_uptrend":            True,
    "require_golden_cross":       True,
    # force_entry: set True only to bypass all conditions for a forced test cycle.
    # Always leave False in normal operation — the hybrid coordinator governs entry.
    "force_entry":                False,
    # Defensive grid — activates a wider grid when RSI oversold in downtrend
    "defensive_mode_enabled":     True,
    "defensive_atr_multiplier":   0.8,
    "defensive_profit_target":    2.5,
    "defensive_num_levels":       5,
    "defensive_rsi_oversold":     40,
    "defensive_rsi_timeframe":    "1h",
    "defensive_rsi_period":       14,
}

# ── Trend Following parameters ─────────────────────────────────────────────────
# capital_allocated is the MAX configured allocation.
# The HybridCoordinator scales it by RS/100 at cycle-open time.
# With RS=31.7, the strategy will idle (min_regime_score=61 blocks entry) — correct.
# To force a test entry, temporarily set force_entry: True (not implemented yet —
# use manage_trading.py to update the strategy metadata if needed).
TREND_FOLLOW_PARAMS = {
    "capital_allocated":    500,    # max quote to deploy per trend cycle
    "risk_pct_per_trade":   1.0,    # % of capital_allocated to risk (for position sizing)
    "atr_stop_multiplier":  2.0,    # initial SL = entry - N×ATR
    "atr_trail_multiplier": 3.0,    # trailing SL = highest - N×ATR
    "min_adx":              25.0,   # minimum ADX(14) for entry
    "min_regime_score":     61.0,   # minimum RS — trend strategy idles below this
    "rsi_entry_max":        70.0,   # skip if RSI overbought
    "max_atr_pct_entry":    6.0,    # skip if extreme volatility
    "reserve_capital_pct":  20,
    "maker_fee_pct":        0.0025,
    "taker_fee_pct":        0.005,
}

# ── Strategy definitions ───────────────────────────────────────────────────────
STRATEGIES = [
    {
        "name":          "BTC/USDT DCA Grid — Paper",
        "strategy_type": "DCA_GRID",
        "base_asset_id": BTC_ID,
        "quote_asset_id": USDT_ID,
        "params":        DCA_GRID_PARAMS,
    },
    {
        "name":          "SOL/USDT DCA Grid — Paper",
        "strategy_type": "DCA_GRID",
        "base_asset_id": SOL_ID,
        "quote_asset_id": USDT_ID,
        "params":        DCA_GRID_PARAMS,
    },
    {
        "name":          "BTC/USDT Trend Follow — Paper",
        "strategy_type": "TREND_FOLLOW",
        "base_asset_id": BTC_ID,
        "quote_asset_id": USDT_ID,
        "params":        TREND_FOLLOW_PARAMS,
    },
    {
        "name":          "SOL/USDT Trend Follow — Paper",
        "strategy_type": "TREND_FOLLOW",
        "base_asset_id": SOL_ID,
        "quote_asset_id": USDT_ID,
        "params":        TREND_FOLLOW_PARAMS,
    },
]


async def upsert_strategy(conn, s: dict, venue_id: int, dry_run: bool) -> None:
    existing = await conn.fetchrow(
        "SELECT id, status FROM base.trade_strategies WHERE name = $1 AND deleted_at IS NULL",
        s["name"],
    )
    taker_fee = s["params"].get("taker_fee_pct", 0.005)

    if existing:
        strat_id = existing["id"]
        status   = existing["status"]
        print(f"  EXISTS  id={strat_id} status={status}  '{s['name']}'")
        if status != "ACTIVE" and not dry_run:
            await conn.execute(
                "UPDATE base.trade_strategies SET status='ACTIVE', updated_at=NOW() WHERE id=$1",
                strat_id,
            )
            print(f"          → Re-activated")
    elif dry_run:
        print(f"  DRY RUN Would create '{s['name']}'")
    else:
        strat_id = await conn.fetchval(
            """
            INSERT INTO base.trade_strategies
                (name, strategy_type, base_asset_id, quote_asset_id,
                 venue_id, taker_fee_pct, status, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, 'ACTIVE', $7::jsonb)
            RETURNING id
            """,
            s["name"], s["strategy_type"],
            s["base_asset_id"], s["quote_asset_id"],
            venue_id, taker_fee,
            json.dumps(s["params"]),
        )
        print(f"  CREATED id={strat_id}  '{s['name']}'")


async def run(dry_run: bool) -> None:
    conn = await get_conn()
    try:
        # ── 1. Venue ──────────────────────────────────────────────────────────
        venue = await conn.fetchrow(
            "SELECT id FROM base.venues WHERE name = $1 AND deleted_at IS NULL",
            VENUE_NAME,
        )
        if venue:
            venue_id = venue["id"]
            print(f"Venue:  EXISTS  id={venue_id}  '{VENUE_NAME}'\n")
        elif dry_run:
            print(f"Venue:  DRY RUN Would create '{VENUE_NAME}'\n")
            venue_id = 0
        else:
            venue_id = await conn.fetchval(
                """
                INSERT INTO base.venues (name, venue_type, source_id, metadata)
                VALUES ($1, 'CEFI_EXCHANGE', $2, '{}')
                RETURNING id
                """,
                VENUE_NAME, CRYPTOCOM_SOURCE_ID,
            )
            print(f"Venue:  CREATED id={venue_id}  '{VENUE_NAME}'\n")

        # ── 2. Strategies ─────────────────────────────────────────────────────
        print("Strategies:")
        for s in STRATEGIES:
            await upsert_strategy(conn, s, venue_id, dry_run)

        print()
        if not dry_run:
            print("Setup complete. Run the bot with:")
            print()
            print("  # Start pricing bot (must run before trader bot)")
            print("  make pricing-bot")
            print()
            print("  # Start trader bot (in a separate terminal)")
            print("  make trader-bot")
            print()
            print("Notes:")
            print("  - TREND_FOLLOW strategies will idle until RS >= 61 (currently trending market needed)")
            print("  - DCA_GRID capital is scaled by (100-RS)/100 at each cycle open")
            print("  - Use 'make manage-trading' to inspect/modify strategy metadata")

    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Set up paper trading venue + hybrid strategies (DCA Grid + Trend Follow)"
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be done, no writes")
    args = p.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
