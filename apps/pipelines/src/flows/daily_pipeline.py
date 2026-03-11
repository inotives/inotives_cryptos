"""
Daily data pipeline orchestrator.

Sequences the full daily data refresh in order:
  1. Fetch OHLCV from CoinGecko → base.asset_metrics_1d
  2. Compute technical indicators  → base.asset_indicators_1d
  3. Compute market regime scores  → base.asset_market_regime

Running as a single flow guarantees each step always has fresh upstream data,
without relying on time-based schedule buffers between separate deployments.

Schedule: daily at 02:00 UTC (configured in main.py deployments).
"""

from datetime import date, datetime, timedelta, timezone

from prefect import flow, get_run_logger

from src.flows.coingecko_ohlcv_1d import coingecko_fetch_ohlcv_1d_flow
from src.flows.compute_indicators_1d import compute_indicators_daily_flow
from src.flows.compute_market_regime_1d import compute_market_regime_daily_flow


@flow(name="daily-data-pipeline", log_prints=True)
async def daily_pipeline_flow(target_date: date | None = None) -> None:
    """
    Full daily data pipeline: OHLCV fetch → indicators → regime scores.

    target_date: override the date to process (default: yesterday UTC).
                 Useful for manual backfill of a specific day.
    """
    logger = get_run_logger()

    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    logger.info("=== Daily pipeline starting for %s ===", target_date)

    # Step 1 — Fetch OHLCV
    logger.info("Step 1/3: Fetching OHLCV from CoinGecko...")
    await coingecko_fetch_ohlcv_1d_flow(target_date=target_date)

    # Step 2 — Compute indicators (runs after OHLCV is guaranteed written)
    logger.info("Step 2/3: Computing technical indicators...")
    await compute_indicators_daily_flow()

    # Step 3 — Compute regime scores (runs after indicators are guaranteed written)
    logger.info("Step 3/3: Computing market regime scores...")
    await compute_market_regime_daily_flow()

    logger.info("=== Daily pipeline complete for %s ===", target_date)
