"""
Hybrid Grid + Trend Following capital coordinator.

Implements the regime-switching capital allocation logic described in the
Hybrid Grid Master Plan. Both DcaGridStrategy and TrendFollowingStrategy
call into this module before opening new cycles.

Capital split (Sliding Scale):
  Grid_Limit  = ((100 - RS) / 100) * configured_capital
  Trend_Limit = (RS / 100) * configured_capital

  RS 0–30  : ~100% grid capital, trend gets little/nothing
  RS 31–60 : sliding scale — both strategies share capital
  RS 61–100: ~100% trend capital, grid paused

Priority rules:
  RS > 50 : Trend takes priority.
             If a TREND_FOLLOW cycle is OPEN for this base asset,
             DCA_GRID must not open a new cycle.
  RS < 50 : Grid takes priority. Trend may only enter if the configured
             trend capital is still available (checked by min_regime_score).
  RS >= 61: Grid is fully paused (factor ≈ 0–39% of capital).

Transition rule (Trend → Grid):
  When RS drops below 61, DO NOT hard-close open trend positions.
  Let them exit via trailing stop naturally.
  Grid begins placing orders while trend positions wind down.
  This is enforced by checking the OPEN cycle, not the trend limit.

Intraday circuit breaker:
  If current_price deviates from the daily open by > 2 × ATR,
  the regime score is overridden to 0 for the current tick.
  This prevents new cycle opens during intraday flash moves.
  Only applies to new-cycle decisions — existing open cycles are NOT
  force-closed by this module (the strategy's own circuit breaker handles that).
"""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Regime thresholds
REGIME_GRID_PAUSE    = 61.0   # RS >= this → grid fully paused
REGIME_PRIORITY_PIVOT = 50.0  # RS > this → trend has priority

# Circuit breaker: how many ATRs from daily open triggers an override
CIRCUIT_BREAKER_ATR_MULT = 2.0


async def get_regime_score_with_circuit_breaker(
    conn,
    asset_id: int,
    base_asset_id: int,
    quote_asset_id: int,
) -> float | None:
    """
    Fetch the latest regime score and apply the intraday circuit breaker.

    Returns:
      - 0.0 if the circuit breaker is active (extreme intraday deviation)
      - The latest final_regime_score otherwise
      - None if no regime data exists yet
    """
    regime_row = await conn.fetchrow(
        """
        SELECT final_regime_score
        FROM base.asset_market_regime
        WHERE asset_id = $1
          AND final_regime_score IS NOT NULL
        ORDER BY metric_date DESC
        LIMIT 1
        """,
        asset_id,
    )
    if not regime_row:
        return None

    regime_score = float(regime_row["final_regime_score"])

    # Intraday circuit breaker check
    if await _circuit_breaker_active(conn, asset_id, base_asset_id, quote_asset_id):
        logger.warning(
            "asset_id=%d: intraday circuit breaker active — overriding RS %.1f → 0.0",
            asset_id, regime_score,
        )
        return 0.0

    return regime_score


async def _circuit_breaker_active(
    conn,
    asset_id: int,
    base_asset_id: int,
    quote_asset_id: int,
) -> bool:
    """
    Returns True if current price has moved > CIRCUIT_BREAKER_ATR_MULT × ATR
    from the most recent daily open price.
    """
    # Get daily open from latest asset_metrics_1d row
    metrics_row = await conn.fetchrow(
        """
        SELECT open_price
        FROM base.asset_metrics_1d
        WHERE asset_id = $1 AND is_final = true
        ORDER BY metric_date DESC
        LIMIT 1
        """,
        asset_id,
    )
    if not metrics_row or not metrics_row["open_price"]:
        return False

    # Get current live price
    price_row = await conn.fetchrow(
        """
        SELECT observed_price
        FROM base.price_observations
        WHERE base_asset_id = $1 AND quote_asset_id = $2
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        base_asset_id, quote_asset_id,
    )
    if not price_row:
        return False

    # Get ATR
    atr_row = await conn.fetchrow(
        """
        SELECT atr_14
        FROM base.asset_indicators_1d
        WHERE asset_id = $1 AND atr_14 IS NOT NULL
        ORDER BY metric_date DESC
        LIMIT 1
        """,
        asset_id,
    )
    if not atr_row or not atr_row["atr_14"]:
        return False

    daily_open    = float(metrics_row["open_price"])
    current_price = float(price_row["observed_price"])
    atr_14        = float(atr_row["atr_14"])

    deviation = abs(current_price - daily_open)
    threshold = CIRCUIT_BREAKER_ATR_MULT * atr_14

    if deviation > threshold:
        logger.warning(
            "Circuit breaker: deviation=%.4f > %.1f×ATR=%.4f "
            "(daily_open=%.4f current=%.4f)",
            deviation, CIRCUIT_BREAKER_ATR_MULT, threshold,
            daily_open, current_price,
        )
        return True

    return False


def grid_capital_limit(configured_capital: Decimal, regime_score: float) -> Decimal:
    """
    Scale grid capital down by regime score.
    RS=0  → 100% of configured_capital
    RS=50 → 50%
    RS=61 → 39% (near zero — grid effectively paused at cycle-open level)
    RS=100→ 0%
    """
    factor = Decimal(str(max(0.0, (100.0 - regime_score) / 100.0)))
    return configured_capital * factor


def trend_capital_limit(configured_capital: Decimal, regime_score: float) -> Decimal:
    """
    Scale trend capital up with regime score.
    RS=61 → 61% of configured_capital
    RS=80 → 80%
    RS=100→ 100%
    """
    factor = Decimal(str(max(0.0, regime_score / 100.0)))
    return configured_capital * factor


async def trend_has_priority(conn, base_asset_id: int, regime_score: float) -> bool:
    """
    Returns True when the trend strategy holds execution priority,
    meaning a new DCA Grid cycle must not be opened.

    Condition: RS > 50  AND  an OPEN TREND_FOLLOW cycle exists for this asset.
    """
    if regime_score <= REGIME_PRIORITY_PIVOT:
        return False

    active = await conn.fetchval(
        """
        SELECT 1
        FROM base.trade_cycles  tc
        JOIN base.trade_strategies ts ON ts.id = tc.strategy_id
        WHERE ts.base_asset_id = $1
          AND ts.strategy_type = 'TREND_FOLLOW'
          AND tc.status        = 'OPEN'
          AND ts.deleted_at    IS NULL
        LIMIT 1
        """,
        base_asset_id,
    )
    return active is not None


async def grid_has_active_cycle(conn, base_asset_id: int) -> bool:
    """
    Returns True if any DCA_GRID cycle is currently OPEN for this asset.
    Used by trend strategy to verify capital priority when RS 31–60.
    """
    active = await conn.fetchval(
        """
        SELECT 1
        FROM base.trade_cycles  tc
        JOIN base.trade_strategies ts ON ts.id = tc.strategy_id
        WHERE ts.base_asset_id = $1
          AND ts.strategy_type = 'DCA_GRID'
          AND tc.status        = 'OPEN'
          AND ts.deleted_at    IS NULL
        LIMIT 1
        """,
        base_asset_id,
    )
    return active is not None
