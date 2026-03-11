"""
Pricing Bot — polls exchange price tickers at a configurable interval
and writes observations to base.price_observations.

Usage:
    # Single pair
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python -m pricing_bot.main \\
        --exchange-id cryptocom --source-code exchange:cryptocom \\
        --pair btc/usdt

    # Multiple pairs
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python -m pricing_bot.main \\
        --exchange-id cryptocom --source-code exchange:cryptocom \\
        --pair btc/usdt --pair eth/usdt --pair sol/usdt --pair cro/usdt

    # Custom interval
    uv run --env-file configs/envs/.env.local --project apps/bots \\
        python -m pricing_bot.main \\
        --exchange-id cryptocom --source-code exchange:cryptocom \\
        --pair btc/usdt --interval 30
"""

import argparse
import asyncio
import logging

from common.config import settings
from common.connections import get_exchange
from common.db import close_pool, get_conn, init_pool

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60  # default, overridden by --interval


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pricing bot — polls exchange tickers and stores observations")
    p.add_argument(
        "--exchange-id",
        default="cryptocom",
        help="ccxt exchange ID (default: cryptocom)",
    )
    p.add_argument(
        "--source-code",
        default="exchange:cryptocom",
        help="base.data_sources.source_code (default: exchange:cryptocom)",
    )
    p.add_argument(
        "--pair",
        dest="pairs",
        action="append",
        metavar="BASE/QUOTE",
        help="Trading pair in BASE/QUOTE format, e.g. btc/usdt. Repeat for multiple pairs.",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=f"Poll interval in seconds (default: {POLL_INTERVAL_SECONDS})",
    )
    args = p.parse_args()

    # Default pairs when none specified
    if not args.pairs:
        args.pairs = ["btc/usdt", "eth/usdt", "sol/usdt", "cro/usdt"]

    return args


def build_watch_pairs(raw_pairs: list[str]) -> list[tuple[str, str, str]]:
    """
    Convert ["btc/usdt", "sol/usdt"] to [(base_code, quote_code, ccxt_symbol), ...].
    ccxt_symbol is uppercased, e.g. "BTC/USDT".
    """
    result = []
    for raw in raw_pairs:
        if "/" not in raw:
            raise ValueError(f"Invalid pair '{raw}' — expected BASE/QUOTE format, e.g. btc/usdt")
        base, quote = raw.lower().split("/", 1)
        symbol = f"{base.upper()}/{quote.upper()}"
        result.append((base, quote, symbol))
    return result


async def load_ids(
    source_code: str,
    watch_pairs: list[tuple[str, str, str]],
) -> tuple[int, dict[str, tuple[int, int]]]:
    """Resolve source_id and asset_ids from DB."""
    async with get_conn() as conn:
        source_row = await conn.fetchrow(
            "SELECT id FROM base.data_sources WHERE source_code = $1", source_code
        )
        if not source_row:
            raise ValueError(f"Data source '{source_code}' not found in DB.")
        source_id: int = source_row["id"]

        pair_map: dict[str, tuple[int, int]] = {}
        for base_code, quote_code, symbol in watch_pairs:
            base_row = await conn.fetchrow(
                "SELECT id FROM base.assets WHERE code = $1", base_code
            )
            quote_row = await conn.fetchrow(
                "SELECT id FROM base.assets WHERE code = $1", quote_code
            )
            if base_row and quote_row:
                pair_map[symbol] = (base_row["id"], quote_row["id"])
            else:
                logger.warning(
                    "Asset not found for pair %s (base=%s, quote=%s), skipping.",
                    symbol, base_code, quote_code,
                )

    return source_id, pair_map


async def fetch_and_store(exchange, source_id: int, pair_map: dict[str, tuple[int, int]]) -> None:
    """Fetch tickers for all watched pairs and insert price observations."""
    symbols = list(pair_map.keys())
    tickers = await exchange.fetch_tickers(symbols)

    async with get_conn() as conn:
        rows = []
        for symbol, (base_asset_id, quote_asset_id) in pair_map.items():
            t = tickers.get(symbol)
            if not t or t.get("last") is None:
                logger.warning("No usable price returned for %s, skipping.", symbol)
                continue
            rows.append((
                source_id,
                base_asset_id,
                quote_asset_id,
                t["last"],
                t["bid"],
                t["ask"],
                t["spread_pct"],
                t["volume_24h"],
                t["timestamp"],
            ))

        await conn.executemany(
            """
            INSERT INTO base.price_observations
                (source_id, base_asset_id, quote_asset_id,
                 observed_price, bid_price, ask_price, spread_pct, volume_24h, observed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (source_id, base_asset_id, quote_asset_id, observed_at) DO NOTHING
            """,
            rows,
        )
        logger.info("Stored %d price observations.", len(rows))


async def run(args: argparse.Namespace) -> None:
    watch_pairs = build_watch_pairs(args.pairs)

    await init_pool()
    exchange = get_exchange(
        args.exchange_id,
        api_key=settings.cryptocom_api_key,
        secret=settings.cryptocom_api_secret,
    )

    try:
        source_id, pair_map = await load_ids(args.source_code, watch_pairs)
        logger.info(
            "Pricing bot started. exchange=%s  source=%s  pairs=%s  interval=%ds",
            args.exchange_id, args.source_code,
            list(pair_map.keys()), args.interval,
        )

        while True:
            try:
                await fetch_and_store(exchange, source_id, pair_map)
            except Exception as exc:
                logger.error("Error fetching prices: %s", exc, exc_info=True)

            await asyncio.sleep(args.interval)
    finally:
        await exchange.close()
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(parse_args()))
