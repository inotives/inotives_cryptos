"""
Daily technical indicators module.

Reads OHLCV from inotives_tradings.asset_metrics_1d, computes technical indicators,
and upserts results into inotives_tradings.asset_indicators_1d.

Indicators computed:
  Volatility  : ATR(14), ATR(20), ATR%(14), ATR SMA(20), volatility_regime
  Trend MAs   : SMA(20), SMA(50), SMA(200), EMA(12), EMA(26), EMA(50), EMA(200)
  MACD        : MACD line, signal(9), histogram
  Momentum    : RSI(14)
  Bands       : Bollinger Bands(20, 2σ), BB width %
  Volume      : Volume SMA(20), volume ratio
  Regime      : ADX(14), EMA slope 5d%, volatility ratio ATR(14)/StdDev(14)
"""

import datetime
import logging
import math

import pandas as pd
import pandas_ta as ta

from common.config import settings
from common.db import get_conn, init_pool, close_pool, is_pool_initialized

logger = logging.getLogger(__name__)

# Minimum rows needed before we attempt computation.
# SMA(200) is the most demanding — we need 200+ closes.
MIN_ROWS_REQUIRED = 220

# ATR regime thresholds (ATR(14) relative to its own 20-day SMA)
ATR_REGIME_THRESHOLDS = {
    "low":     0.75,   # atr_14 < 75% of atr_sma_20
    "normal":  1.25,   # 75% ≤ atr_14 ≤ 125% of atr_sma_20
    "high":    2.00,   # 125% < atr_14 ≤ 200% of atr_sma_20
    # > 200% → extreme
}


# ── Core functions ─────────────────────────────────────────────────────────────

async def load_ohlcv(asset_id: int, lookback_days: int = 400) -> pd.DataFrame:
    """
    Fetch the last N days of OHLCV from inotives_tradings.asset_metrics_1d for one asset.
    Returns a DataFrame sorted ascending by metric_date.
    """
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT metric_date,
                   open_price, high_price, low_price, close_price,
                   volume_usd
            FROM inotives_tradings.asset_metrics_1d
            WHERE asset_id = $1
              AND is_final  = true
            ORDER BY metric_date DESC
            LIMIT $2
            """,
            asset_id, lookback_days,
        )

    if not rows:
        logger.warning("No OHLCV rows found for asset_id=%d.", asset_id)
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"]   = pd.to_datetime(df["date"])
    df[["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)
    # Rows arrived newest-first; reverse to chronological order for indicator computation
    df = df.sort_values("date").reset_index(drop=True)

    logger.info("Loaded %d OHLCV rows for asset_id=%d.", len(df), asset_id)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators from OHLCV DataFrame.
    Returns a new DataFrame with one row per date containing indicator values.
    Rows with insufficient history will have NaN for that indicator.
    """
    if df.empty or len(df) < 2:
        return pd.DataFrame()

    out = pd.DataFrame({"date": df["date"]})

    # ── Volatility: ATR ───────────────────────────────────────────────────────
    out["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    out["atr_20"] = ta.atr(df["high"], df["low"], df["close"], length=20)

    # ATR as % of closing price
    out["atr_pct"] = (out["atr_14"] / df["close"] * 100).round(6)

    # 20-day SMA of ATR(14) — the baseline for regime classification
    out["atr_sma_20"] = out["atr_14"].rolling(window=20, min_periods=20).mean()

    # Volatility regime: compare today's ATR to its rolling average
    def classify_regime(row) -> str | None:
        if pd.isna(row["atr_14"]) or pd.isna(row["atr_sma_20"]):
            return None
        ratio = row["atr_14"] / row["atr_sma_20"]
        if ratio < ATR_REGIME_THRESHOLDS["low"]:
            return "low"
        if ratio <= ATR_REGIME_THRESHOLDS["normal"]:
            return "normal"
        if ratio <= ATR_REGIME_THRESHOLDS["high"]:
            return "high"
        return "extreme"

    out["volatility_regime"] = out.apply(classify_regime, axis=1)

    # ── Trend: Moving Averages ────────────────────────────────────────────────
    out["sma_20"]  = ta.sma(df["close"], length=20)
    out["sma_50"]  = ta.sma(df["close"], length=50)
    out["sma_200"] = ta.sma(df["close"], length=200)
    out["ema_12"]  = ta.ema(df["close"], length=12)
    out["ema_26"]  = ta.ema(df["close"], length=26)
    out["ema_50"]  = ta.ema(df["close"], length=50)
    out["ema_200"] = ta.ema(df["close"], length=200)

    # ── MACD ──────────────────────────────────────────────────────────────────
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        out["macd"]        = macd_df.iloc[:, 0]  # MACD_12_26_9
        out["macd_signal"] = macd_df.iloc[:, 2]  # MACDs_12_26_9
        out["macd_hist"]   = macd_df.iloc[:, 1]  # MACDh_12_26_9
    else:
        out["macd"] = out["macd_signal"] = out["macd_hist"] = None

    # ── Momentum: RSI ─────────────────────────────────────────────────────────
    out["rsi_14"] = ta.rsi(df["close"], length=14)

    # ── Volatility Bands: Bollinger Bands ─────────────────────────────────────
    bb_df = ta.bbands(df["close"], length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        out["bb_lower"]  = bb_df.iloc[:, 0]   # BBL_20_2.0
        out["bb_middle"] = bb_df.iloc[:, 1]   # BBM_20_2.0
        out["bb_upper"]  = bb_df.iloc[:, 2]   # BBU_20_2.0
        # BB width as % of middle band
        out["bb_width"] = ((out["bb_upper"] - out["bb_lower"]) / out["bb_middle"] * 100).round(6)
    else:
        out["bb_lower"] = out["bb_middle"] = out["bb_upper"] = out["bb_width"] = None

    # ── Volume ────────────────────────────────────────────────────────────────
    out["volume_sma_20"] = df["volume"].rolling(window=20, min_periods=20).mean().round(2)
    out["volume_ratio"]  = (df["volume"] / out["volume_sma_20"]).round(6)

    # ── Regime Detection ──────────────────────────────────────────────────────
    # ADX(14) — trend strength (0–100, direction-agnostic)
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None and not adx_df.empty:
        out["adx_14"] = adx_df.iloc[:, 0].round(6)  # ADX_14
    else:
        out["adx_14"] = None

    # EMA slope 5d% — rate of change of EMA(50) over 5 days
    # Formula: ((EMA50_today - EMA50_5d_ago) / EMA50_5d_ago) * 100
    ema50_shifted = out["ema_50"].shift(5)
    out["ema_slope_5d"] = ((out["ema_50"] - ema50_shifted) / ema50_shifted * 100).round(6)

    # Volatility ratio — ATR(14) / StdDev(Close, 14)
    # Low ratio (<0.8) → smooth trending move (high regime score)
    # High ratio (>1.2) → choppy, mean-reverting noise (low regime score)
    stddev_14 = df["close"].rolling(window=14, min_periods=14).std()
    out["vol_ratio_14"] = (out["atr_14"] / stddev_14).round(6)

    return out


async def upsert_indicators(
    asset_id: int,
    indicators: pd.DataFrame,
    target_dates: list | None = None,
) -> int:
    """
    Upsert computed indicators into inotives_tradings.asset_indicators_1d.

    target_dates: if provided, only upsert rows for those dates (e.g. today only).
                  If None, upserts all rows (used for backfill).
    """
    if indicators.empty:
        logger.warning("No indicator rows to upsert for asset_id=%d.", asset_id)
        return 0

    df = indicators.copy()
    if target_dates:
        df = df[df["date"].dt.date.isin(target_dates)]

    # Drop rows where close/ATR are all NaN (no usable data)
    df = df.dropna(subset=["atr_14"], how="all")

    if df.empty:
        logger.info("No computable rows after filtering for asset_id=%d.", asset_id)
        return 0

    def _float(val):
        """Return None for NaN/None, else Python float."""
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _str(val):
        """Return None for NaN/None, else str."""
        if val is None:
            return None
        try:
            if isinstance(val, float) and math.isnan(val):
                return None
        except (TypeError, ValueError):
            pass
        return str(val)

    async with get_conn() as conn:
        upserted = 0
        for _, row in df.iterrows():
            await conn.execute(
                """
                INSERT INTO inotives_tradings.asset_indicators_1d (
                    asset_id, metric_date,
                    atr_14, atr_20, atr_pct, atr_sma_20, volatility_regime,
                    sma_20, sma_50, sma_200, ema_12, ema_26, ema_50, ema_200,
                    macd, macd_signal, macd_hist,
                    rsi_14,
                    bb_upper, bb_middle, bb_lower, bb_width,
                    volume_sma_20, volume_ratio,
                    adx_14, ema_slope_5d, vol_ratio_14
                ) VALUES (
                    $1, $2,
                    $3, $4, $5, $6, $7,
                    $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17,
                    $18,
                    $19, $20, $21, $22,
                    $23, $24,
                    $25, $26, $27
                )
                ON CONFLICT (asset_id, metric_date) DO UPDATE SET
                    atr_14            = EXCLUDED.atr_14,
                    atr_20            = EXCLUDED.atr_20,
                    atr_pct           = EXCLUDED.atr_pct,
                    atr_sma_20        = EXCLUDED.atr_sma_20,
                    volatility_regime = EXCLUDED.volatility_regime,
                    sma_20            = EXCLUDED.sma_20,
                    sma_50            = EXCLUDED.sma_50,
                    sma_200           = EXCLUDED.sma_200,
                    ema_12            = EXCLUDED.ema_12,
                    ema_26            = EXCLUDED.ema_26,
                    ema_50            = EXCLUDED.ema_50,
                    ema_200           = EXCLUDED.ema_200,
                    macd              = EXCLUDED.macd,
                    macd_signal       = EXCLUDED.macd_signal,
                    macd_hist         = EXCLUDED.macd_hist,
                    rsi_14            = EXCLUDED.rsi_14,
                    bb_upper          = EXCLUDED.bb_upper,
                    bb_middle         = EXCLUDED.bb_middle,
                    bb_lower          = EXCLUDED.bb_lower,
                    bb_width          = EXCLUDED.bb_width,
                    volume_sma_20     = EXCLUDED.volume_sma_20,
                    volume_ratio      = EXCLUDED.volume_ratio,
                    adx_14            = EXCLUDED.adx_14,
                    ema_slope_5d      = EXCLUDED.ema_slope_5d,
                    vol_ratio_14      = EXCLUDED.vol_ratio_14
                """,
                asset_id, row["date"].date(),
                _float(row.get("atr_14")),    _float(row.get("atr_20")),
                _float(row.get("atr_pct")),   _float(row.get("atr_sma_20")),
                _str(row.get("volatility_regime")),
                _float(row.get("sma_20")),    _float(row.get("sma_50")),
                _float(row.get("sma_200")),   _float(row.get("ema_12")),
                _float(row.get("ema_26")),    _float(row.get("ema_50")),
                _float(row.get("ema_200")),
                _float(row.get("macd")),      _float(row.get("macd_signal")),
                _float(row.get("macd_hist")),
                _float(row.get("rsi_14")),
                _float(row.get("bb_upper")),  _float(row.get("bb_middle")),
                _float(row.get("bb_lower")),  _float(row.get("bb_width")),
                _float(row.get("volume_sma_20")), _float(row.get("volume_ratio")),
                _float(row.get("adx_14")),    _float(row.get("ema_slope_5d")),
                _float(row.get("vol_ratio_14")),
            )
            upserted += 1

    logger.info("Upserted %d indicator rows for asset_id=%d.", upserted, asset_id)
    return upserted


# ── Entry points ──────────────────────────────────────────────────────────────

async def _load_assets(asset_codes: list[str] | None = None) -> list[dict]:
    """Load assets to process (by code or all with metrics data)."""
    async with get_conn() as conn:
        if asset_codes:
            rows = await conn.fetch(
                "SELECT id, code FROM inotives_tradings.assets WHERE code = ANY($1::text[])",
                asset_codes,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT DISTINCT a.id, a.code
                FROM inotives_tradings.assets a
                JOIN inotives_tradings.asset_metrics_1d m ON m.asset_id = a.id
                ORDER BY a.code
                """
            )
    return [dict(r) for r in rows]


async def run_indicators_daily(asset_codes: list[str] | None = None) -> None:
    """
    Nightly run: recompute indicators for today's date only.
    Runs after the OHLCV fetch has completed.
    """
    own_pool = False
    if not is_pool_initialized():
        await init_pool()
        own_pool = True

    try:
        today = datetime.date.today()
        assets = await _load_assets(asset_codes)

        for asset in assets:
            df = await load_ohlcv(asset["id"], lookback_days=400)
            if df.empty or len(df) < MIN_ROWS_REQUIRED:
                logger.warning("Skipping %s — insufficient history.", asset["code"])
                continue
            indicators = compute_indicators(df)
            await upsert_indicators(asset["id"], indicators, target_dates=[today])
    finally:
        if own_pool:
            await close_pool()


async def run_indicators_backfill(asset_codes: list[str] | None = None) -> None:
    """
    One-time backfill: compute indicators for all historical dates in metrics_1d.
    """
    own_pool = False
    if not is_pool_initialized():
        await init_pool()
        own_pool = True

    try:
        assets = await _load_assets(asset_codes)
        logger.info("Backfilling indicators for %d asset(s).", len(assets))

        for asset in assets:
            logger.info("Processing asset: %s (id=%d)", asset["code"], asset["id"])
            df = await load_ohlcv(asset["id"], lookback_days=99_999)
            if df.empty or len(df) < MIN_ROWS_REQUIRED:
                logger.warning(
                    "Skipping %s — only %d rows (need %d).",
                    asset["code"], len(df), MIN_ROWS_REQUIRED,
                )
                continue
            indicators = compute_indicators(df)
            await upsert_indicators(asset["id"], indicators)
    finally:
        if own_pool:
            await close_pool()
