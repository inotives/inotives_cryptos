"""
One-time setup for paper trading.

Creates:
  1. A venue row for Crypto.com (if it doesn't exist)
  2. A DCA_GRID strategy row for BTC/USDT with status=ACTIVE

Safe to re-run — checks before inserting.

Usage:
    uv run --env-file configs/envs/.env.local python scripts/setup_paper_trading.py [--dry-run]
"""

import argparse
import asyncio
import json
import os
import sys

import asyncpg


async def get_conn():
    return await asyncpg.connect(
        host     = os.environ["DB_HOST"],
        port     = int(os.environ.get("DB_PORT", 5432)),
        user     = os.environ["DB_USER"],
        password = os.environ["DB_PASSWORD"],
        database = os.environ["DB_NAME"],
    )


# Strategy parameters — same shape as DEFAULT_PARAMS in backtest/runner.py.
# Tune these before starting the bot.
STRATEGY_PARAMS = {
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
    # Trend filters — set False to allow entries regardless of SMA position.
    # Useful for paper trading in bear/sideways markets.
    "require_uptrend":            False,
    "require_golden_cross":       False,
    # Set True to bypass ALL entry conditions and open a cycle immediately.
    # Overrides require_uptrend, require_golden_cross, RSI, ATR, and regime checks.
    # Remember to set back to False after the forced entry.
    "force_entry":                True,
    # Defensive grid mode — instead of idling in a downtrend, enter with a
    # wider/safer grid when a bounce signal (RSI oversold) is detected.
    "defensive_mode_enabled":    True,
    "defensive_atr_multiplier":  0.8,
    "defensive_profit_target":   2.5,
    "defensive_num_levels":      5,
    "defensive_rsi_oversold":    40,
    "defensive_rsi_timeframe":   "1h",
    "defensive_rsi_period":      14,
}

VENUE_NAME          = "Crypto.com (Paper)"
STRATEGY_NAME       = "BTC/USDT DCA Grid — Paper"
BASE_ASSET_ID       = 26    # btc
QUOTE_ASSET_ID      = 134   # usdt
CRYPTOCOM_SOURCE_ID = 1     # exchange:cryptocom


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
            print(f"Venue already exists: id={venue_id}  '{VENUE_NAME}'")
        elif dry_run:
            print(f"[DRY RUN] Would insert venue '{VENUE_NAME}'")
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
            print(f"Created venue: id={venue_id}  '{VENUE_NAME}'")

        # ── 2. Strategy ───────────────────────────────────────────────────────
        existing = await conn.fetchrow(
            """
            SELECT id, status FROM base.trade_strategies
            WHERE name = $1 AND deleted_at IS NULL
            """,
            STRATEGY_NAME,
        )

        if existing:
            print(
                f"Strategy already exists: id={existing['id']}  "
                f"status={existing['status']}  '{STRATEGY_NAME}'"
            )
            if existing["status"] != "ACTIVE" and not dry_run:
                await conn.execute(
                    "UPDATE base.trade_strategies SET status='ACTIVE', updated_at=NOW() WHERE id=$1",
                    existing["id"],
                )
                print(f"  → Re-activated strategy id={existing['id']}")
        elif dry_run:
            print(f"[DRY RUN] Would insert strategy '{STRATEGY_NAME}'")
        else:
            strat_id = await conn.fetchval(
                """
                INSERT INTO base.trade_strategies
                    (name, strategy_type, base_asset_id, quote_asset_id,
                     venue_id, taker_fee_pct, status, metadata)
                VALUES ($1, 'DCA_GRID', $2, $3, $4, $5, 'ACTIVE', $6)
                RETURNING id
                """,
                STRATEGY_NAME,
                BASE_ASSET_ID,
                QUOTE_ASSET_ID,
                venue_id,
                STRATEGY_PARAMS["taker_fee_pct"],
                json.dumps(STRATEGY_PARAMS),
            )
            print(f"Created strategy: id={strat_id}  '{STRATEGY_NAME}'")

        print()
        if not dry_run:
            print("Setup complete. Start the bot with:")
            print()
            print("  uv run --env-file configs/envs/.env.local --project apps/bots \\")
            print("      python -m trader_bot.main")

    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Set up paper trading venue + strategy")
    p.add_argument("--dry-run", action="store_true", help="Print what would be done, no writes")
    args = p.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
