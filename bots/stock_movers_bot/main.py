"""
Stock Top Movers scraper bot.

Fetches top gaining and losing stocks from Yahoo Finance and stores them
in the database for analysis.

Usage:
    uv run --env-file configs/envs/.env.local python -m bots.stock_movers_bot.main
    uv run --env-file configs/envs/.env.local python -m bots.stock_movers_bot.main --limit 50

Cron example (hourly):
    0 * * * * cd /path/to/inotives && uv run --env-file configs/envs/.env.local python -m bots.stock_movers_bot.main
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone

import yfinance as yf

from common.db import init_pool, close_pool, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def fetch_top_movers(limit: int = 25) -> tuple[list[dict], list[dict]]:
    """
    Fetch top gainers and losers from Yahoo Finance.

    Uses yfinance's get_day_gainers() and get_day_losers() methods
    which scrape Yahoo Finance's market movers pages.

    Args:
        limit: Maximum number of movers to fetch per category.

    Returns:
        Tuple of (gainers, losers) lists of dicts.
    """
    logger.info("Fetching top %d gainers and losers from Yahoo Finance...", limit)

    gainers = []
    losers = []

    try:
        gainers_df = yf.get_day_gainers()
        if gainers_df is not None and not gainers_df.empty:
            gainers = _parse_movers_dataframe(gainers_df, "gainer", limit)
            logger.info("Fetched %d gainers.", len(gainers))
    except Exception as e:
        logger.error("Failed to fetch gainers: %s", e)

    try:
        losers_df = yf.get_day_losers()
        if losers_df is not None and not losers_df.empty:
            losers = _parse_movers_dataframe(losers_df, "loser", limit)
            logger.info("Fetched %d losers.", len(losers))
    except Exception as e:
        logger.error("Failed to fetch losers: %s", e)

    return gainers, losers


def _parse_movers_dataframe(df, movers_type: str, limit: int) -> list[dict]:
    """Parse yfinance DataFrame into list of dicts."""
    movers = []

    for _, row in df.head(limit).iterrows():
        mover = {
            "symbol": _safe_str(row.get("Symbol")),
            "name": _safe_str(row.get("Name")),
            "price": _safe_float(row.get("Price")),
            "change_percent": _safe_float(row.get("% Change")),
            "volume": _safe_int(row.get("Volume")),
            "market_cap": _safe_int(row.get("Market Cap")),
            "movers_type": movers_type,
        }
        if mover["symbol"]:
            movers.append(mover)

    return movers


def _safe_str(value) -> str | None:
    if value is None:
        return None
    return str(value) if str(value) != "nan" else None


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        return f if f == f else None
    except (ValueError, TypeError):
        return None


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        f = float(value)
        return int(f) if f == f else None
    except (ValueError, TypeError):
        return None


async def store_movers(gainers: list[dict], losers: list[dict]) -> int:
    """
    Store movers data in the database.

    Args:
        gainers: List of gainer dicts.
        losers: List of loser dicts.

    Returns:
        Total number of rows inserted.
    """
    if not gainers and not losers:
        logger.warning("No movers to store.")
        return 0

    fetched_at = datetime.now(timezone.utc)
    total_inserted = 0

    async with get_conn() as conn:
        all_movers = gainers + losers

        for mover in all_movers:
            try:
                await conn.execute(
                    """
                    INSERT INTO inotives_tradings.stock_top_movers
                    (symbol, name, price, change_percent, volume, market_cap, movers_type, fetched_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    mover["symbol"],
                    mover["name"],
                    mover["price"],
                    mover["change_percent"],
                    mover["volume"],
                    mover["market_cap"],
                    mover["movers_type"],
                    fetched_at,
                )
                total_inserted += 1
            except Exception as e:
                logger.error("Failed to insert %s: %s", mover.get("symbol"), e)

    logger.info(
        "Stored %d movers (fetched_at=%s).", total_inserted, fetched_at.isoformat()
    )
    return total_inserted


async def main(limit: int = 25) -> None:
    """
    Main entry point for the stock movers bot.

    Args:
        limit: Maximum number of movers to fetch per category.
    """
    logger.info("Starting stock top movers scraper (limit=%d)...", limit)

    await init_pool()
    try:
        gainers, losers = await fetch_top_movers(limit)
        await store_movers(gainers, losers)
        logger.info("Stock top movers scraper complete.")
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape top stock movers from Yahoo Finance."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of movers to fetch per category. Defaults to 25.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.limit))
