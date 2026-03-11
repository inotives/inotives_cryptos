"""
Backtest parameter sweep — runs a set of named grid configurations across one
or more date windows and saves every result to base.backtest_runs.

Usage:
    # Full sweep, all configs × all windows, results saved to DB
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python scripts/run_backtest_sweep.py

    # Single config by name, no DB write
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python scripts/run_backtest_sweep.py --config balanced --no-save

    # Different asset (default: asset_id=1 = BTC)
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python scripts/run_backtest_sweep.py --asset-id 2

Options:
    --asset-id INT    base.assets.id to backtest  [default: 1]
    --config  NAME    run only this named config   [default: all]
    --window  NAME    run only this date window    [default: all]
    --capital FLOAT   initial capital              [default: 10000]
    --no-save         skip writing to base.backtest_runs
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

# Make apps/bots packages (backtest, common) importable when this script is
# invoked from the repo root with: uv run --project apps/bots python scripts/...
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "bots"))

from common.db import close_pool, init_pool  # noqa: E402
from backtest.runner import run_backtest      # noqa: E402


# ---------------------------------------------------------------------------
# Named parameter configurations
# ---------------------------------------------------------------------------

CONFIGS: dict[str, dict] = {

    # ── Balanced (baseline) ────────────────────────────────────────────────
    # Mirrors DEFAULT_PARAMS exactly.  Use as the reference line.
    "balanced": {
        "capital_per_cycle":          1_000,
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
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    8.0,
        "max_expansions":             1,
        "expansion_levels":           2,
        "expansion_capital_fraction": 0.3,
    },

    # ── Conservative ──────────────────────────────────────────────────────
    # Tighter grid → more frequent fills; lower targets → quicker cycle
    # turnover; stricter entry filter; no expansion to limit drawdown.
    "conservative": {
        "capital_per_cycle":          1_000,
        "num_levels":                 5,
        "weights":                    [1, 1, 1, 2, 2],
        "atr_multiplier_low":         0.3,
        "atr_multiplier_normal":      0.35,
        "atr_multiplier_high":        0.5,
        "profit_target_low":          0.8,
        "profit_target_normal":       1.0,
        "profit_target_high":         1.8,
        "max_atr_pct_entry":          5.0,   # stricter: only enter calm markets
        "rsi_entry_max":              55,    # stricter: avoid overbought entries
        "reserve_capital_pct":        40,
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    7.0,
        "max_expansions":             0,     # no crash expansion
        "expansion_levels":           2,
        "expansion_capital_fraction": 0.0,
    },

    # ── Aggressive ────────────────────────────────────────────────────────
    # Wider grid spacing → levels are further apart, each fill is cheaper;
    # higher profit target → hold for bigger moves; looser entry filter.
    "aggressive": {
        "capital_per_cycle":          1_500,
        "num_levels":                 3,
        "weights":                    [1, 2, 3],
        "atr_multiplier_low":         0.6,
        "atr_multiplier_normal":      0.8,
        "atr_multiplier_high":        1.0,
        "profit_target_low":          2.0,
        "profit_target_normal":       3.0,
        "profit_target_high":         5.0,
        "max_atr_pct_entry":          8.0,
        "rsi_entry_max":              65,
        "reserve_capital_pct":        20,
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    10.0,
        "max_expansions":             2,
        "expansion_levels":           3,
        "expansion_capital_fraction": 0.4,
    },

    # ── Deep Grid (Fibonacci weighting) ───────────────────────────────────
    # 7 levels with Fibonacci weights → most capital sits at the deepest
    # levels where asymmetric reward is largest during strong pullbacks.
    "deep_grid": {
        "capital_per_cycle":          1_200,
        "num_levels":                 7,
        "weights":                    [1, 1, 2, 3, 5, 8, 13],
        "atr_multiplier_low":         0.4,
        "atr_multiplier_normal":      0.5,
        "atr_multiplier_high":        0.7,
        "profit_target_low":          1.5,
        "profit_target_normal":       2.0,
        "profit_target_high":         3.5,
        "max_atr_pct_entry":          6.0,
        "rsi_entry_max":              60,
        "reserve_capital_pct":        30,
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    8.0,
        "max_expansions":             1,
        "expansion_levels":           2,
        "expansion_capital_fraction": 0.3,
    },

    # ── Scalper ───────────────────────────────────────────────────────────
    # Very tight spacing + very low profit target = many short cycles that
    # each capture a small move.  Only viable in choppy sideways markets.
    "scalper": {
        "capital_per_cycle":          800,
        "num_levels":                 3,
        "weights":                    [1, 1, 2],
        "atr_multiplier_low":         0.2,
        "atr_multiplier_normal":      0.25,
        "atr_multiplier_high":        0.4,
        "profit_target_low":          0.4,
        "profit_target_normal":       0.6,
        "profit_target_high":         1.2,
        "max_atr_pct_entry":          4.0,   # only enter in calm conditions
        "rsi_entry_max":              50,    # only enter below midline
        "reserve_capital_pct":        35,
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    6.0,
        "max_expansions":             0,
        "expansion_levels":           2,
        "expansion_capital_fraction": 0.0,
    },

    # ── Crash Hunter ──────────────────────────────────────────────────────
    # Wide spacing + aggressive expansion = designed to accumulate heavily
    # during bear-market capitulation and ride the recovery.
    "crash_hunter": {
        "capital_per_cycle":          1_000,
        "num_levels":                 5,
        "weights":                    [1, 1, 2, 4, 6],   # heavy bottom weighting
        "atr_multiplier_low":         0.5,
        "atr_multiplier_normal":      0.7,
        "atr_multiplier_high":        1.0,
        "profit_target_low":          2.0,
        "profit_target_normal":       3.0,
        "profit_target_high":         5.0,
        "max_atr_pct_entry":          7.0,
        "rsi_entry_max":              60,
        "reserve_capital_pct":        25,
        "taker_fee_pct":              0.001,
        "circuit_breaker_atr_pct":    12.0,  # tolerates more volatility
        "max_expansions":             3,
        "expansion_levels":           3,
        "expansion_capital_fraction": 0.5,
    },
}


# ---------------------------------------------------------------------------
# Date windows
# ---------------------------------------------------------------------------

WINDOWS: dict[str, tuple[date, date]] = {
    "bull_2020_2021":  (date(2020, 1,  1), date(2021, 12, 31)),  # parabolic bull
    "bear_2022":       (date(2022, 1,  1), date(2022, 12, 31)),  # brutal bear
    "recovery_2023":   (date(2023, 1,  1), date(2023, 12, 31)),  # slow grind up
    "cycle_2023_2024": (date(2023, 1,  1), date(2024, 12, 31)),  # recent full cycle
    "long_2020_2024":  (date(2020, 1,  1), date(2024, 12, 31)),  # 5-year view
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

METRIC_COLS = [
    ("total_return_pct",       "return%",    "8.2f"),
    ("max_drawdown_pct",       "max_dd%",    "7.2f"),
    ("win_rate",               "win%",       "6.1f"),
    ("profit_factor",          "pf",         "6.2f"),
    ("sharpe_ratio",           "sharpe",     "7.3f"),
    ("total_cycles",           "cycles",     "6d"),
    ("avg_cycle_duration_secs","avg_dur_d",  "8.1f"),
]

def _fmt_val(key: str, val, fmt: str) -> str:
    if val is None:
        return "  --  "
    if fmt.endswith("d"):
        return f"{int(val):{fmt}}"
    if "f" in fmt:
        # avg_cycle_duration_secs stored as seconds → display as days
        if key == "avg_cycle_duration_secs":
            return f"{val / 86400:{fmt}}"
        return f"{val:{fmt}}"
    return str(val)

def _print_header():
    cols = "  ".join(f"{label:>{max(len(label), 8)}}" for _, label, fmt in METRIC_COLS)
    print(f"\n{'config':<16}  {'window':<18}  {cols}")
    print("-" * (16 + 2 + 18 + 2 + sum(max(len(l), 8) + 2 for _, l, _ in METRIC_COLS)))

def _print_row(config: str, window: str, metrics: dict):
    vals = "  ".join(
        f"{_fmt_val(k, metrics.get(k), fmt):>{max(len(label), 8)}}"
        for k, label, fmt in METRIC_COLS
    )
    print(f"{config:<16}  {window:<18}  {vals}")


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

async def run_sweep(
    asset_id:        int,
    config_filter:   str | None,
    window_filter:   str | None,
    initial_capital: float,
    save_to_db:      bool,
) -> None:
    configs = (
        {config_filter: CONFIGS[config_filter]}
        if config_filter else CONFIGS
    )
    windows = (
        {window_filter: WINDOWS[window_filter]}
        if window_filter else WINDOWS
    )

    _print_header()

    for cfg_name, params in configs.items():
        for win_name, (start, end) in windows.items():
            run_name = f"{cfg_name} / {win_name}"
            try:
                metrics = await run_backtest(
                    asset_id        = asset_id,
                    name            = run_name,
                    start_date      = start,
                    end_date        = end,
                    parameters      = params,
                    initial_capital = initial_capital,
                    save_to_db      = save_to_db,
                )
                _print_row(cfg_name, win_name, metrics)
            except ValueError as exc:
                print(f"  {cfg_name:<16}  {win_name:<18}  SKIP — {exc}")
            except Exception as exc:
                print(f"  {cfg_name:<16}  {win_name:<18}  ERROR — {exc}")

    print()
    if save_to_db:
        print("Results saved to base.backtest_runs.")
    else:
        print("Results not saved (--no-save).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DCA grid parameter sweep")
    p.add_argument("--asset-id", type=int,   default=1,        help="base.assets.id")
    p.add_argument("--config",   type=str,   default=None,     help=f"One of: {', '.join(CONFIGS)}")
    p.add_argument("--window",   type=str,   default=None,     help=f"One of: {', '.join(WINDOWS)}")
    p.add_argument("--capital",  type=float, default=10_000.0, help="Initial capital")
    p.add_argument("--no-save",  action="store_true",          help="Skip DB write")
    return p.parse_args()


async def _main() -> None:
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    args = _parse_args()

    if args.config and args.config not in CONFIGS:
        print(f"Unknown config '{args.config}'. Choose from: {', '.join(CONFIGS)}")
        sys.exit(1)
    if args.window and args.window not in WINDOWS:
        print(f"Unknown window '{args.window}'. Choose from: {', '.join(WINDOWS)}")
        sys.exit(1)

    await init_pool()
    try:
        await run_sweep(
            asset_id        = args.asset_id,
            config_filter   = args.config,
            window_filter   = args.window,
            initial_capital = args.capital,
            save_to_db      = not args.no_save,
        )
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
