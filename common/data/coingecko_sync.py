"""
CoinGecko reference data sync module.

Fetches the full platform list and coin list from CoinGecko and upserts
into the coingecko schema (raw_platforms, raw_coins).

Used by bootstrap and can be scheduled via cron for weekly refresh.
"""

import json
import logging
from datetime import datetime, timezone

from common.api.coingecko import CoinGeckoClient
from common.config import settings
from common.db import get_conn, init_pool, close_pool, is_pool_initialized

logger = logging.getLogger(__name__)


# ── Platforms ──────────────────────────────────────────────────────────────────

def fetch_platforms() -> list[dict]:
    """
    Call GET /asset_platforms.
    Returns [{id, chain_identifier, name, shortname, native_coin_id, image}, ...].
    """
    client = CoinGeckoClient(
        api_key=settings.coingecko_api_key,
        key_type=settings.coingecko_api_key_type,
    )
    platforms = client.get_asset_platforms()
    logger.info("Fetched %d asset platforms from CoinGecko.", len(platforms))
    return platforms


async def upsert_raw_platforms(platforms: list[dict]) -> int:
    """Bulk upsert platform list into coingecko.raw_platforms."""
    fetched_at = datetime.now(timezone.utc)

    async with get_conn() as conn:
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

    logger.info("Upserted %d platforms into coingecko.raw_platforms.", len(platforms))
    return len(platforms)


async def run_sync_platforms() -> int:
    """Fetch the full CoinGecko asset platform list and upsert into coingecko.raw_platforms."""
    own_pool = False
    if not is_pool_initialized():
        await init_pool()
        own_pool = True

    try:
        platforms = fetch_platforms()
        total = await upsert_raw_platforms(platforms)
        return total
    finally:
        if own_pool:
            await close_pool()


# ── Coins list ─────────────────────────────────────────────────────────────────

def fetch_coins_list() -> list[dict]:
    """
    Call GET /coins/list?include_platform=true.
    Returns [{id, symbol, name, platforms: {chain: address}}, ...].
    """
    client = CoinGeckoClient(
        api_key=settings.coingecko_api_key,
        key_type=settings.coingecko_api_key_type,
    )
    coins = client.get_coins_list(include_platform=True)
    logger.info("Fetched %d coins from CoinGecko /coins/list.", len(coins))
    return coins


async def upsert_raw_coins(coins: list[dict]) -> int:
    """Bulk upsert coin list into coingecko.raw_coins."""
    fetched_at = datetime.now(timezone.utc)

    async with get_conn() as conn:
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
                    json.dumps(coin.get("platforms") or {}),
                    fetched_at,
                )
                for coin in coins
                if coin.get("id")
            ],
        )

    logger.info("Upserted %d coins into coingecko.raw_coins.", len(coins))
    return len(coins)


async def run_sync_coins_list() -> int:
    """Fetch the full CoinGecko coin list and upsert into coingecko.raw_coins."""
    own_pool = False
    if not is_pool_initialized():
        await init_pool()
        own_pool = True

    try:
        coins = fetch_coins_list()
        total = await upsert_raw_coins(coins)
        return total
    finally:
        if own_pool:
            await close_pool()
