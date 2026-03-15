"""
Daily data pipeline bot.

Runs the full daily sequence:
  1. Fetch OHLCV from CoinGecko → inotives_tradings.asset_metrics_1d
  2. Compute technical indicators → inotives_tradings.asset_indicators_1d
  3. Compute market regime scores → inotives_tradings.asset_market_regime

Usage:
  uv run --env-file configs/envs/.env.local python -m bots.data_bot.main
  uv run --env-file configs/envs/.env.local python -m bots.data_bot.main --date 2026-03-13

Cron example (02:00 UTC daily):
  0 2 * * * cd /path/to/inotives && uv run --env-file configs/envs/.env.local python -m bots.data_bot.main
"""

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from common.db import init_pool, close_pool
from common.data.ohlcv import run_ohlcv_fetch
from common.data.indicators import run_indicators_daily
from common.data.market_regime import run_regime_daily

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main(target_date: date | None = None) -> None:
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    logger.info("Starting daily data pipeline for %s.", target_date)

    await init_pool()
    try:
        logger.info("[1/3] Fetching OHLCV from CoinGecko...")
        await run_ohlcv_fetch(target_date)

        logger.info("[2/3] Computing technical indicators...")
        await run_indicators_daily()

        logger.info("[3/3] Computing market regime scores...")
        await run_regime_daily()

        logger.info("Daily data pipeline complete for %s.", target_date)
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run daily data pipeline.")
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Target date (YYYY-MM-DD). Defaults to yesterday UTC.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.date))
