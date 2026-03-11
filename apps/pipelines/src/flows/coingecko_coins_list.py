"""
CoinGecko coins list sync flow.

Fetches the full coin list from GET /coins/list?include_platform=true
and upserts into coingecko.raw_coins.

Covers ~13,000+ coins with their CoinGecko ID, symbol, name, and
platform contract addresses.

Schedule: weekly on Monday at 00:30 UTC (configured in prefect.yaml).
"""

from datetime import datetime, timezone

import asyncpg
from prefect import flow, task, get_run_logger

from src.api import CoinGeckoClient
from src.config import settings


# ── Tasks ──────────────────────────────────────────────────────────────────────

@task(name="fetch-coingecko-coins-list", retries=3, retry_delay_seconds=60)
def fetch_coins_list() -> list[dict]:
    """
    Call GET /coins/list?include_platform=true.
    Returns [{id, symbol, name, platforms: {chain: address}}, ...].
    """
    logger = get_run_logger()
    client = CoinGeckoClient(
        api_key=settings.coingecko_api_key,
        key_type=settings.coingecko_api_key_type,
    )
    coins = client.get_coins_list(include_platform=True)
    logger.info("Fetched %d coins from CoinGecko /coins/list.", len(coins))
    return coins


@task(name="upsert-coingecko-raw-coins")
async def upsert_raw_coins(coins: list[dict]) -> int:
    """
    Bulk upsert coin list into coingecko.raw_coins.
    Updates symbol, name, platforms, updated_at, and fetched_at on conflict.
    """
    logger = get_run_logger()
    fetched_at = datetime.now(timezone.utc)

    conn = await asyncpg.connect(settings.db_dsn)
    try:
        await conn.executemany(
            """
            INSERT INTO coingecko.raw_coins
                (coingecko_id, symbol, name, platforms, fetched_at, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $5)
            ON CONFLICT (coingecko_id) DO UPDATE SET
                symbol     = EXCLUDED.symbol,
                name       = EXCLUDED.name,
                platforms  = EXCLUDED.platforms,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = EXCLUDED.updated_at
            """,
            [
                (
                    coin["id"],
                    coin.get("symbol", ""),
                    coin.get("name", ""),
                    str(coin.get("platforms") or {}).replace("'", '"'),
                    fetched_at,
                )
                for coin in coins
                if coin.get("id")
            ],
        )
    finally:
        await conn.close()

    logger.info("Upserted %d coins into coingecko.raw_coins.", len(coins))
    return len(coins)


# ── Flow ──────────────────────────────────────────────────────────────────────

@flow(name="coingecko-sync-coins-list", log_prints=True)
async def coingecko_sync_coins_list_flow() -> int:
    """
    Fetch the full CoinGecko coin list and upsert into coingecko.raw_coins.
    Schedule: weekly on Monday at 00:30 UTC.
    """
    coins = fetch_coins_list()
    total = await upsert_raw_coins(coins)
    return total
