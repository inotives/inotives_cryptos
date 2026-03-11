"""
CoinGecko asset platforms sync flow.

Fetches the full platform list from GET /asset_platforms and upserts
into coingecko.raw_platforms.

Each platform represents a blockchain/network supported by CoinGecko,
with its EVM chain ID (if applicable), native coin, and image URLs.

Schedule: weekly on Monday at 00:00 UTC (configured in prefect.yaml).
"""

from datetime import datetime, timezone

import asyncpg
from prefect import flow, task, get_run_logger

from src.api import CoinGeckoClient
from src.config import settings


# ── Tasks ──────────────────────────────────────────────────────────────────────

@task(name="fetch-coingecko-platforms", retries=3, retry_delay_seconds=60)
def fetch_platforms() -> list[dict]:
    """
    Call GET /asset_platforms.
    Returns [{id, chain_identifier, name, shortname, native_coin_id, image}, ...].
    """
    logger = get_run_logger()
    client = CoinGeckoClient(
        api_key=settings.coingecko_api_key,
        key_type=settings.coingecko_api_key_type,
    )
    platforms = client.get_asset_platforms()
    logger.info("Fetched %d asset platforms from CoinGecko.", len(platforms))
    return platforms


@task(name="upsert-coingecko-raw-platforms")
async def upsert_raw_platforms(platforms: list[dict]) -> int:
    """
    Bulk upsert platform list into coingecko.raw_platforms.
    Updates all fields on conflict.
    """
    logger = get_run_logger()
    fetched_at = datetime.now(timezone.utc)

    conn = await asyncpg.connect(settings.db_dsn)
    try:
        await conn.executemany(
            """
            INSERT INTO coingecko.raw_platforms (
                coingecko_id, chain_identifier, name, shortname, native_coin_id,
                image_thumb, image_small, image_large,
                fetched_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
            ON CONFLICT (coingecko_id) DO UPDATE SET
                chain_identifier = EXCLUDED.chain_identifier,
                name             = EXCLUDED.name,
                shortname        = EXCLUDED.shortname,
                native_coin_id   = EXCLUDED.native_coin_id,
                image_thumb      = EXCLUDED.image_thumb,
                image_small      = EXCLUDED.image_small,
                image_large      = EXCLUDED.image_large,
                fetched_at       = EXCLUDED.fetched_at,
                updated_at       = EXCLUDED.updated_at
            """,
            [
                (
                    p["id"],
                    p.get("chain_identifier"),
                    p.get("name", ""),
                    p.get("shortname") or None,
                    p.get("native_coin_id") or None,
                    (p.get("image") or {}).get("thumb") or None,
                    (p.get("image") or {}).get("small") or None,
                    (p.get("image") or {}).get("large") or None,
                    fetched_at,
                )
                for p in platforms
                if p.get("id")
            ],
        )
    finally:
        await conn.close()

    logger.info("Upserted %d platforms into coingecko.raw_platforms.", len(platforms))
    return len(platforms)


# ── Flow ──────────────────────────────────────────────────────────────────────

@flow(name="coingecko-sync-platforms", log_prints=True)
async def coingecko_sync_platforms_flow() -> int:
    """
    Fetch the full CoinGecko asset platform list and upsert into coingecko.raw_platforms.
    Schedule: weekly on Monday at 00:00 UTC.
    """
    platforms = fetch_platforms()
    total = await upsert_raw_platforms(platforms)
    return total
