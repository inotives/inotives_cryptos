"""
Daily market regime computation pipeline.

Reads pre-computed indicators from base.asset_indicators_1d, normalises
ADX, EMA slope, and volatility ratio into 0–100 component scores, then
computes a weighted final Regime Score.

Regime Score (RS) interpretation:
  0  – 30  : Deep Sideways  → 100% DCA Grid
  31 – 60  : Hybrid/Transition → sliding scale
  61 – 100 : Strong Trend   → 100% Trend Following

Formula:
  RS = (score_adx × 0.4) + (score_slope × 0.4) + (score_vol × 0.2)

Component normalisations:
  ADX (trend strength):
    ≤ 15  → 0     (dead quiet)
    = 25  → 50    (threshold of a trend)
    ≥ 40  → 100   (strong trend)
    Piecewise linear: [15–25] → [0–50], [25–40] → [50–100]

  EMA slope 5d% (trend direction + velocity):
    ≤ 0%   → 0    (flat or downtrend)
    ≥ 0.5% → 100  (strong uptrend)
    Linear scaling between 0 and 0.5.

  Volatility ratio ATR(14)/StdDev(14) (noise filter, inverted):
    ≥ 1.2 → 0    (choppy, mean-reverting)
    ≤ 0.8 → 100  (smooth, directional)
    Linear interpolation between breakpoints.

Schedule: daily, after compute_indicators_1d has run.
"""

import asyncpg
import pandas as pd
from prefect import flow, task, get_run_logger

from src.config import settings


# ── Normalisation helpers ───────────────────────────────────────────────────

def _norm_adx(adx: float | None) -> float | None:
    """Piecewise-linear ADX → 0–100 score."""
    if adx is None:
        return None
    if adx <= 15:
        return 0.0
    if adx <= 25:
        # [15, 25] → [0, 50]
        return (adx - 15) / (25 - 15) * 50
    if adx <= 40:
        # [25, 40] → [50, 100]
        return 50 + (adx - 25) / (40 - 25) * 50
    return 100.0


def _norm_slope(slope: float | None) -> float | None:
    """Linear EMA-slope% → 0–100 score. Negative slope → 0."""
    if slope is None:
        return None
    if slope <= 0:
        return 0.0
    if slope >= 0.5:
        return 100.0
    return slope / 0.5 * 100


def _norm_vol_ratio(ratio: float | None) -> float | None:
    """Inverted linear volatility-ratio → 0–100 score."""
    if ratio is None:
        return None
    if ratio >= 1.2:
        return 0.0
    if ratio <= 0.8:
        return 100.0
    # [0.8, 1.2] → [100, 0]
    return (1.2 - ratio) / (1.2 - 0.8) * 100


def _regime_score(score_adx, score_slope, score_vol) -> float | None:
    """Weighted final score. Returns None if any component is missing."""
    if any(s is None for s in (score_adx, score_slope, score_vol)):
        return None
    return round(score_adx * 0.4 + score_slope * 0.4 + score_vol * 0.2, 6)


# ── Tasks ───────────────────────────────────────────────────────────────────

@task(name="load-regime-inputs", retries=2, retry_delay_seconds=10)
async def load_regime_inputs(
    asset_id: int,
    target_dates: list | None = None,
) -> pd.DataFrame:
    """
    Fetch adx_14, ema_slope_5d, vol_ratio_14 from asset_indicators_1d.

    target_dates: if provided, filter to those dates only (daily mode).
                  If None, load all available rows (backfill mode).
    """
    logger = get_run_logger()
    conn = await asyncpg.connect(settings.db_dsn)
    try:
        if target_dates:
            rows = await conn.fetch(
                """
                SELECT metric_date, adx_14, ema_slope_5d, vol_ratio_14
                FROM base.asset_indicators_1d
                WHERE asset_id = $1
                  AND metric_date = ANY($2::date[])
                ORDER BY metric_date ASC
                """,
                asset_id, target_dates,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT metric_date, adx_14, ema_slope_5d, vol_ratio_14
                FROM base.asset_indicators_1d
                WHERE asset_id = $1
                  AND adx_14 IS NOT NULL
                ORDER BY metric_date ASC
                """,
                asset_id,
            )
    finally:
        await conn.close()

    if not rows:
        logger.warning("No regime input rows for asset_id=%d.", asset_id)
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "adx_14", "ema_slope_5d", "vol_ratio_14"])
    df["date"] = pd.to_datetime(df["date"])
    for col in ("adx_14", "ema_slope_5d", "vol_ratio_14"):
        df[col] = df[col].astype(float, errors="ignore")

    logger.info("Loaded %d regime-input rows for asset_id=%d.", len(df), asset_id)
    return df


@task(name="compute-regime-scores")
def compute_regime_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise indicator values and compute the final Regime Score."""
    if df.empty:
        return pd.DataFrame()

    out = pd.DataFrame({"date": df["date"]})

    out["raw_adx"]       = df["adx_14"]
    out["raw_slope"]     = df["ema_slope_5d"]
    out["raw_vol_ratio"] = df["vol_ratio_14"]

    out["score_adx"]   = df["adx_14"].apply(_norm_adx)
    out["score_slope"] = df["ema_slope_5d"].apply(_norm_slope)
    out["score_vol"]   = df["vol_ratio_14"].apply(_norm_vol_ratio)

    out["final_regime_score"] = out.apply(
        lambda r: _regime_score(r["score_adx"], r["score_slope"], r["score_vol"]),
        axis=1,
    )

    return out


@task(name="upsert-regime-scores", retries=2, retry_delay_seconds=10)
async def upsert_regime_scores(
    asset_id: int,
    scores: pd.DataFrame,
) -> int:
    """Upsert computed regime scores into base.asset_market_regime."""
    logger = get_run_logger()

    if scores.empty:
        logger.warning("No regime score rows to upsert for asset_id=%d.", asset_id)
        return 0

    import math

    def _f(val):
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) else round(f, 6)
        except (TypeError, ValueError):
            return None

    conn = await asyncpg.connect(settings.db_dsn)
    upserted = 0
    try:
        for _, row in scores.iterrows():
            await conn.execute(
                """
                INSERT INTO base.asset_market_regime (
                    asset_id, metric_date,
                    raw_adx, raw_slope, raw_vol_ratio,
                    score_adx, score_slope, score_vol,
                    final_regime_score
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (asset_id, metric_date) DO UPDATE SET
                    raw_adx            = EXCLUDED.raw_adx,
                    raw_slope          = EXCLUDED.raw_slope,
                    raw_vol_ratio      = EXCLUDED.raw_vol_ratio,
                    score_adx          = EXCLUDED.score_adx,
                    score_slope        = EXCLUDED.score_slope,
                    score_vol          = EXCLUDED.score_vol,
                    final_regime_score = EXCLUDED.final_regime_score
                """,
                asset_id, row["date"].date(),
                _f(row.get("raw_adx")),       _f(row.get("raw_slope")),
                _f(row.get("raw_vol_ratio")),
                _f(row.get("score_adx")),     _f(row.get("score_slope")),
                _f(row.get("score_vol")),
                _f(row.get("final_regime_score")),
            )
            upserted += 1
    finally:
        await conn.close()

    logger.info("Upserted %d regime rows for asset_id=%d.", upserted, asset_id)
    return upserted


# ── Flows ────────────────────────────────────────────────────────────────────

@flow(name="compute-market-regime-1d-backfill", log_prints=True)
async def compute_market_regime_backfill_flow(asset_codes: list[str] | None = None) -> None:
    """
    One-time backfill: compute regime scores for all historical dates
    that have adx_14 populated in asset_indicators_1d.

    asset_codes: list of codes to process (e.g. ['btc', 'eth']).
                 If None, processes all assets with indicator data.
    """
    logger = get_run_logger()

    conn = await asyncpg.connect(settings.db_dsn)
    try:
        if asset_codes:
            assets = await conn.fetch(
                "SELECT id, code FROM base.assets WHERE code = ANY($1::text[])",
                asset_codes,
            )
        else:
            assets = await conn.fetch(
                """
                SELECT DISTINCT a.id, a.code
                FROM base.assets a
                JOIN base.asset_indicators_1d i ON i.asset_id = a.id
                WHERE i.adx_14 IS NOT NULL
                ORDER BY a.code
                """
            )
    finally:
        await conn.close()

    logger.info("Backfilling regime scores for %d asset(s).", len(assets))

    for asset in assets:
        logger.info("Processing asset: %s (id=%d)", asset["code"], asset["id"])
        df = await load_regime_inputs(asset["id"])
        scores = compute_regime_scores(df)
        await upsert_regime_scores(asset["id"], scores)


@flow(name="compute-market-regime-1d-daily", log_prints=True)
async def compute_market_regime_daily_flow(asset_codes: list[str] | None = None) -> None:
    """
    Nightly run: compute regime scores for today only.
    Runs after compute_indicators_daily_flow has completed.
    """
    import datetime
    logger = get_run_logger()
    today = datetime.date.today()

    conn = await asyncpg.connect(settings.db_dsn)
    try:
        if asset_codes:
            assets = await conn.fetch(
                "SELECT id, code FROM base.assets WHERE code = ANY($1::text[])",
                asset_codes,
            )
        else:
            assets = await conn.fetch(
                """
                SELECT DISTINCT a.id, a.code
                FROM base.assets a
                JOIN base.asset_indicators_1d i ON i.asset_id = a.id
                ORDER BY a.code
                """
            )
    finally:
        await conn.close()

    for asset in assets:
        df = await load_regime_inputs(asset["id"], target_dates=[today])
        if df.empty:
            logger.warning("No indicator data for %s on %s — skipping.", asset["code"], today)
            continue
        scores = compute_regime_scores(df)
        await upsert_regime_scores(asset["id"], scores)
