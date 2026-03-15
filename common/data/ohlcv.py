"""
Daily OHLCV fetch module.

Fetches yesterday's completed daily candle for all assets mapped to
api:coingecko in inotives_tradings.asset_source_mappings, then upserts into
inotives_tradings.asset_metrics_1d.

CoinGecko endpoints used:
  GET /coins/{id}/ohlc?vs_currency=usd&days=90
    → 4h candles aggregated to daily O, H, L, C.
  GET /coins/{id}/market_chart?vs_currency=usd&days=91
    → daily granularity (days > 90 required on free tier).
    → provides volume_usd and market_cap_usd.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone

from common.api.coingecko import CoinGeckoClient
from common.config import settings
from common.db import get_conn, init_pool, close_pool, is_pool_initialized

logger = logging.getLogger(__name__)

COINGECKO_SOURCE_CODE = "api:coingecko"

# days=30 → 4h candles from /ohlc (aggregated to daily).
# days >= 90 on demo tier returns 4-day candles, losing daily granularity.
OHLCV_DAYS = 30
# days=91 → daily granularity from /market_chart (free-tier threshold is > 90)
MARKET_CHART_DAYS = 91

MAX_RETRIES = 3
RETRY_DELAY = 60  # seconds


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_daily_value(series: list[list], target_date: date) -> float | None:
    """Return the value from a [[timestamp_ms, value], ...] series matching target_date."""
    for ts_ms, value in series:
        if datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date() == target_date:
            return value
    return None


def _retry_fetch(fn, *args, description: str = ""):
    """Retry a fetch function up to MAX_RETRIES times with RETRY_DELAY between attempts."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args)
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %ds...",
                attempt, MAX_RETRIES, description, e, RETRY_DELAY,
            )
            time.sleep(RETRY_DELAY)


# ── Core functions ─────────────────────────────────────────────────────────────

async def load_asset_mappings() -> list[dict]:
    """
    Load all assets that have a CoinGecko mapping from inotives_tradings.asset_source_mappings.
    Returns list of {asset_id, asset_code, coingecko_id}.
    """
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id AS asset_id, a.code AS asset_code,
                   asm.source_identifier AS coingecko_id
            FROM inotives_tradings.asset_source_mappings asm
            JOIN inotives_tradings.assets       a  ON a.id  = asm.asset_id
            JOIN inotives_tradings.data_sources ds ON ds.id = asm.source_id
            WHERE ds.source_code = $1
              AND asm.deleted_at IS NULL
            ORDER BY a.code
            """,
            COINGECKO_SOURCE_CODE,
        )

    mappings = [dict(r) for r in rows]
    logger.info("Found %d assets mapped to CoinGecko.", len(mappings))
    return mappings


def fetch_ohlcv_for_asset(coingecko_id: str) -> list[list]:
    """
    Call GET /coins/{id}/ohlc?vs_currency=usd&days=90.
    Returns [[timestamp_ms, open, high, low, close], ...] sorted ascending.
    """
    client = CoinGeckoClient(api_key=settings.coingecko_api_key, key_type=settings.coingecko_api_key_type)

    def _fetch():
        candles = client.get_ohlcv(coingecko_id, days=OHLCV_DAYS)
        logger.info("Fetched %d OHLC candles for %s.", len(candles), coingecko_id)
        return candles

    return _retry_fetch(_fetch, description=f"OHLCV fetch for {coingecko_id}")


def fetch_market_chart_for_asset(coingecko_id: str) -> dict:
    """
    Call GET /coins/{id}/market_chart?vs_currency=usd&days=91.
    days > 90 forces daily granularity on the free tier.
    Returns {prices, market_caps, total_volumes} as [[timestamp_ms, value], ...].
    """
    client = CoinGeckoClient(api_key=settings.coingecko_api_key, key_type=settings.coingecko_api_key_type)

    def _fetch():
        chart = client.get_market_chart(coingecko_id, days=MARKET_CHART_DAYS)
        logger.info(
            "Fetched market chart for %s: %d volume points, %d market_cap points.",
            coingecko_id,
            len(chart.get("total_volumes", [])),
            len(chart.get("market_caps", [])),
        )
        return chart

    return _retry_fetch(_fetch, description=f"market chart fetch for {coingecko_id}")


async def upsert_ohlcv(
    asset_id:       int,
    source_id:      int,
    candles:        list[list],
    market_chart:   dict,
    target_date:    date,
) -> int:
    """
    Upsert a single day's OHLCV + market cap into inotives_tradings.asset_metrics_1d.

    OHLC comes from the /ohlc candles (4h candles aggregated to daily).
    volume_usd and market_cap_usd come from /market_chart (daily series).
    """
    # ── Aggregate 4h candles → daily OHLC ────────────────────────────────────
    day_candles = [
        c for c in candles
        if datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc).date() == target_date
    ]

    if not day_candles:
        logger.warning("No OHLC candles for asset_id=%d on %s — skipping.", asset_id, target_date)
        return 0

    open_price  = day_candles[0][1]
    high_price  = max(c[2] for c in day_candles)
    low_price   = min(c[3] for c in day_candles)
    close_price = day_candles[-1][4]

    # ── Extract volume + market cap from market chart ─────────────────────────
    volume_usd     = _extract_daily_value(market_chart.get("total_volumes", []), target_date)
    market_cap_usd = _extract_daily_value(market_chart.get("market_caps",   []), target_date)

    if volume_usd is None:
        logger.warning(
            "No volume data for asset_id=%d on %s in market chart — volume_usd will be NULL.",
            asset_id, target_date,
        )
    if market_cap_usd is None:
        logger.warning(
            "No market cap data for asset_id=%d on %s in market chart — market_cap_usd will be NULL.",
            asset_id, target_date,
        )

    async with get_conn() as conn:
        # Fetch previous close for price_change_pct
        prev_close = await conn.fetchval(
            """
            SELECT close_price FROM inotives_tradings.asset_metrics_1d
            WHERE asset_id = $1 AND source_id = $2 AND metric_date = $3
            """,
            asset_id, source_id, target_date - timedelta(days=1),
        )
        price_change_pct = None
        if prev_close:
            price_change_pct = round(
                (close_price - float(prev_close)) / float(prev_close) * 100, 6
            )

        await conn.execute(
            """
            INSERT INTO inotives_tradings.asset_metrics_1d (
                asset_id, source_id, metric_date,
                open_price, high_price, low_price, close_price,
                volume_usd, market_cap_usd,
                price_change_pct, is_final, metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, true, '{}')
            ON CONFLICT (asset_id, metric_date, source_id) DO UPDATE SET
                open_price       = EXCLUDED.open_price,
                high_price       = EXCLUDED.high_price,
                low_price        = EXCLUDED.low_price,
                close_price      = EXCLUDED.close_price,
                volume_usd       = EXCLUDED.volume_usd,
                market_cap_usd   = EXCLUDED.market_cap_usd,
                price_change_pct = EXCLUDED.price_change_pct,
                is_final         = EXCLUDED.is_final
            """,
            asset_id, source_id,
            target_date, open_price, high_price, low_price, close_price,
            volume_usd, market_cap_usd,
            price_change_pct,
        )

    logger.info(
        "Upserted %s OHLCV for asset_id=%d | close=%.4f vol=%s mcap=%s",
        target_date, asset_id, close_price,
        f"{volume_usd:,.0f}" if volume_usd else "NULL",
        f"{market_cap_usd:,.0f}" if market_cap_usd else "NULL",
    )
    return 1


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_ohlcv_fetch(target_date: date | None = None) -> int:
    """
    Fetch and store yesterday's daily OHLCV from CoinGecko for all mapped assets.

    Makes two API calls per asset:
      1. /ohlc          → O, H, L, C
      2. /market_chart  → volume_usd, market_cap_usd

    target_date: override which date to fetch (default: yesterday UTC).
    """
    own_pool = False
    if not is_pool_initialized():
        await init_pool()
        own_pool = True

    try:
        if target_date is None:
            target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        logger.info("Fetching CoinGecko OHLCV for %s.", target_date)

        mappings = await load_asset_mappings()
        if not mappings:
            logger.warning("No CoinGecko asset mappings found — nothing to fetch.")
            return 0

        # Resolve source_id once
        async with get_conn() as conn:
            source_id = await conn.fetchval(
                "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1",
                COINGECKO_SOURCE_CODE,
            )

        if not source_id:
            raise ValueError(f"Data source '{COINGECKO_SOURCE_CODE}' not found in DB.")

        total = 0
        for mapping in mappings:
            candles      = fetch_ohlcv_for_asset(mapping["coingecko_id"])
            market_chart = fetch_market_chart_for_asset(mapping["coingecko_id"])
            upserted     = await upsert_ohlcv(
                mapping["asset_id"], source_id,
                candles, market_chart, target_date,
            )
            total += upserted

        logger.info("Done. %d asset(s) updated for %s.", total, target_date)
        return total
    finally:
        if own_pool:
            await close_pool()
