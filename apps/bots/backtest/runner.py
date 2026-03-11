"""
Backtest runner — loads data from DB, drives the engine, saves results.

CLI usage:
    uv run --env-file configs/envs/.env.local --project apps/bots \
        python -m backtest.runner \
        --asset-id 1 \
        --start 2023-01-01 \
        --end 2024-12-31 \
        --name "BTC baseline 2023-2024" \
        --capital 10000

Programmatic usage:
    from backtest.runner import run_backtest
    metrics = await run_backtest(asset_id=1, name="test", start_date=..., end_date=...)
"""

import argparse
import asyncio
import json
import logging
from datetime import date
from decimal import Decimal

from common.db import close_pool, get_conn, init_pool

from .engine import DcaGridBacktestEngine
from .models import BacktestCandle

logger = logging.getLogger(__name__)

# ── Default parameters ───────────────────────────────────────────────────────

DEFAULT_PARAMS: dict = {
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
    "require_uptrend":            True,
    "require_golden_cross":       True,
    "force_entry":                False,
    "defensive_mode_enabled":    False,
    "defensive_atr_multiplier":  0.8,
    "defensive_profit_target":   2.5,
    "defensive_num_levels":      5,
    "defensive_rsi_oversold":    30,
}

# ── DB helpers ───────────────────────────────────────────────────────────────

async def load_candles(
    conn,
    asset_id:   int,
    start_date: date,
    end_date:   date,
) -> list[BacktestCandle]:
    rows = await conn.fetch(
        """
        SELECT metric_date, open_price, high_price, low_price, close_price, volume_usd
        FROM base.asset_metrics_1d
        WHERE asset_id  = $1
          AND metric_date >= $2
          AND metric_date <= $3
          AND is_final    = true
        ORDER BY metric_date ASC
        """,
        asset_id, start_date, end_date,
    )
    return [
        BacktestCandle(
            date   = row["metric_date"],
            open   = Decimal(str(row["open_price"])),
            high   = Decimal(str(row["high_price"])),
            low    = Decimal(str(row["low_price"])),
            close  = Decimal(str(row["close_price"])),
            volume = Decimal(str(row["volume_usd"] or 0)),
        )
        for row in rows
    ]


async def load_indicators(
    conn,
    asset_id:   int,
    start_date: date,
    end_date:   date,
) -> dict[date, dict]:
    rows = await conn.fetch(
        """
        SELECT metric_date, atr_14, atr_pct, volatility_regime,
               sma_50, sma_200, rsi_14
        FROM base.asset_indicators_1d
        WHERE asset_id   = $1
          AND metric_date >= $2
          AND metric_date <= $3
          AND atr_14          IS NOT NULL
          AND atr_pct         IS NOT NULL
          AND volatility_regime IS NOT NULL
        ORDER BY metric_date ASC
        """,
        asset_id, start_date, end_date,
    )
    return {row["metric_date"]: dict(row) for row in rows}


# ── Core runner ──────────────────────────────────────────────────────────────

async def run_backtest(
    asset_id:        int,
    name:            str,
    start_date:      date,
    end_date:        date,
    parameters:      dict | None = None,
    initial_capital: float       = 10_000.0,
    save_to_db:      bool        = True,
) -> dict:
    """
    Run a full DCA grid backtest over the given date range.

    Returns a metrics dict with the keys matching base.backtest_runs columns.
    Optionally saves the results to the DB.
    """
    params = {**DEFAULT_PARAMS, **(parameters or {})}

    async with get_conn() as conn:
        candles    = await load_candles(conn, asset_id, start_date, end_date)
        indicators = await load_indicators(conn, asset_id, start_date, end_date)

    if not candles:
        raise ValueError(
            f"No candle data for asset_id={asset_id} between {start_date} and {end_date}. "
            "Run the daily pipeline or seed historical data first."
        )

    logger.info(
        "Backtest '%s': asset=%d  %s → %s  candles=%d  indicators=%d",
        name, asset_id, start_date, end_date, len(candles), len(indicators),
    )

    engine = DcaGridBacktestEngine(params, initial_capital=initial_capital)
    for candle in candles:
        engine.process_candle(candle, indicators.get(candle.date))

    # Force-close any open cycle at the end of the test period
    if engine.active_cycle and candles:
        last = candles[-1]
        engine._close_cycle(last, last.close, "end_of_backtest")

    metrics = engine.compute_metrics()

    logger.info(
        "Backtest '%s' done: return=%.2f%%  max_dd=%.2f%%  win_rate=%.1f%%  "
        "cycles=%d  sharpe=%.3f  profit_factor=%s",
        name,
        metrics["total_return_pct"],
        metrics["max_drawdown_pct"],
        metrics["win_rate"],
        metrics["total_cycles"],
        metrics["sharpe_ratio"],
        metrics["profit_factor"],
    )

    if save_to_db:
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO base.backtest_runs
                    (asset_id, name, timeframe, start_date, end_date,
                     parameters, status, started_at, completed_at,
                     total_return_pct, max_drawdown_pct, win_rate,
                     sharpe_ratio, profit_factor,
                     total_cycles, avg_cycle_duration_secs)
                VALUES ($1, $2, '1d', $3, $4, $5,
                        'COMPLETED', NOW(), NOW(),
                        $6, $7, $8, $9, $10, $11, $12)
                """,
                asset_id, name, start_date, end_date,
                json.dumps(params),
                metrics["total_return_pct"],
                metrics["max_drawdown_pct"],
                metrics["win_rate"],
                metrics["sharpe_ratio"],
                metrics["profit_factor"],
                metrics["total_cycles"],
                metrics["avg_cycle_duration_secs"],
            )
        logger.info("Backtest results saved to base.backtest_runs.")

    return metrics


# ── CLI entry point ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a DCA grid backtest")
    p.add_argument("--asset-id",  type=int,   required=True,  help="base.assets.id")
    p.add_argument("--start",     type=date.fromisoformat, required=True,  help="YYYY-MM-DD")
    p.add_argument("--end",       type=date.fromisoformat, required=True,  help="YYYY-MM-DD")
    p.add_argument("--name",      type=str,   default="backtest run",      help="Human-readable label")
    p.add_argument("--capital",   type=float, default=10_000.0,            help="Initial capital")
    p.add_argument("--params",    type=str,   default=None,                help="JSON string of param overrides")
    p.add_argument("--no-save",   action="store_true",                     help="Skip writing to DB")
    return p.parse_args()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()

    param_overrides = json.loads(args.params) if args.params else None

    await init_pool()
    try:
        metrics = await run_backtest(
            asset_id        = args.asset_id,
            name            = args.name,
            start_date      = args.start,
            end_date        = args.end,
            parameters      = param_overrides,
            initial_capital = args.capital,
            save_to_db      = not args.no_save,
        )
        print("\n── Backtest Results ──────────────────────────────────")
        for k, v in metrics.items():
            print(f"  {k:<30} {v}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
