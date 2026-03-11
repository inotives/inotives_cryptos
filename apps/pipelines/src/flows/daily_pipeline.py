"""
Daily data pipeline orchestrator.

Sequences the full daily data refresh in order:
  1. Fetch OHLCV from CoinGecko → base.asset_metrics_1d
  2. Compute technical indicators   → base.asset_indicators_1d

Running as a single flow guarantees step 2 always has fresh OHLCV data,
without relying on time-based schedule buffers between separate deployments.

Schedule: daily at 02:00 UTC (configured in main.py deployments).
"""

from datetime import date, datetime, timedelta, timezone

from prefect import flow, get_run_logger

from src.flows.coingecko_ohlcv_1d import coingecko_fetch_ohlcv_1d_flow
from src.flows.compute_indicators_1d import compute_indicators_daily_flow


@flow(name="daily-data-pipeline", log_prints=True)
async def daily_pipeline_flow(target_date: date | None = None) -> None:
    """
    Full daily data pipeline: OHLCV fetch → indicator computation.

    target_date: override the date to process (default: yesterday UTC).
                 Useful for manual backfill of a specific day.
    """
    logger = get_run_logger()

    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    logger.info("=== Daily pipeline starting for %s ===", target_date)

    # Step 1 — Fetch OHLCV
    logger.info("Step 1/2: Fetching OHLCV from CoinGecko...")
    await coingecko_fetch_ohlcv_1d_flow(target_date=target_date)

    # Step 2 — Compute indicators (runs after OHLCV is guaranteed written)
    logger.info("Step 2/2: Computing technical indicators...")
    await compute_indicators_daily_flow()

    logger.info("=== Daily pipeline complete for %s ===", target_date)
