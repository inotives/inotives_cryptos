"""
CoinGecko fetcher flows.

Flows:
  - fetch_asset_platforms: Upserts blockchain networks from /asset_platforms endpoint
                           into base.networks + base.network_source_mappings
"""

import asyncpg
from prefect import flow, task, get_run_logger

from src.api import CoinGeckoClient
from src.config import settings

COINGECKO_SOURCE_CODE = "api:coingecko"


# ── Tasks ─────────────────────────────────────────────────────────────────────

@task(name="fetch-asset-platforms", retries=3, retry_delay_seconds=30)
def fetch_asset_platforms_from_api() -> list[dict]:
    """Call CoinGecko /asset_platforms and return raw platform list."""
    logger = get_run_logger()

    client = CoinGeckoClient(api_key=settings.coingecko_api_key, key_type=settings.coingecko_api_key_type)
    platforms = client.get_asset_platforms()

    logger.info("Fetched %d asset platforms from CoinGecko.", len(platforms))
    return platforms


@task(name="upsert-networks")
async def upsert_networks(platforms: list[dict]) -> int:
    """Upsert platforms into base.networks and base.network_source_mappings."""
    logger = get_run_logger()

    conn = await asyncpg.connect(settings.db_dsn)
    upserted = 0

    try:
        # Resolve source_id for coingecko
        source_id = await conn.fetchval(
            "SELECT id FROM base.data_sources WHERE source_code = $1",
            COINGECKO_SOURCE_CODE,
        )
        if not source_id:
            raise ValueError(
                f"Data source '{COINGECKO_SOURCE_CODE}' not found. "
                "Ensure it has been seeded in base.data_sources."
            )

        for platform in platforms:
            cg_id = platform.get("id", "").strip()
            name = platform.get("name", "").strip()

            if not cg_id or not name:
                continue

            # Upsert into base.networks (code = CoinGecko platform id)
            network_id = await conn.fetchval(
                """
                INSERT INTO base.networks (code, name, category)
                VALUES ($1, $2, 'blockchain')
                ON CONFLICT (code) DO UPDATE
                    SET name = EXCLUDED.name
                RETURNING id
                """,
                cg_id, name,
            )

            # Upsert into base.network_source_mappings
            await conn.execute(
                """
                INSERT INTO base.network_source_mappings
                    (network_id, source_id, source_identifier, source_name, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (network_id, source_id) DO UPDATE
                    SET source_identifier = EXCLUDED.source_identifier,
                        source_name       = EXCLUDED.source_name,
                        metadata          = EXCLUDED.metadata
                """,
                network_id,
                source_id,
                cg_id,
                name,
                # Store extra CoinGecko fields in metadata
                str({
                    "chain_identifier": platform.get("chain_identifier"),
                    "shortname": platform.get("shortname"),
                    "native_coin_id": platform.get("native_coin_id"),
                }).replace("'", '"'),  # crude but avoids json import at top
            )
            upserted += 1

    finally:
        await conn.close()

    logger.info("Upserted %d networks.", upserted)
    return upserted


# ── Flow ──────────────────────────────────────────────────────────────────────

@flow(name="coingecko-fetch-asset-platforms", log_prints=True)
async def fetch_asset_platforms_flow() -> None:
    """
    Fetch all blockchain networks (asset platforms) from CoinGecko
    and sync them into base.networks + base.network_source_mappings.

    Schedule: daily (configured in deployments.py)
    """
    platforms = fetch_asset_platforms_from_api()
    await upsert_networks(platforms)
