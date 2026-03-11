"""
DCA Grid strategy implementation.

Volatility-adaptive DCA grid that uses ATR-based spacing and weighted capital
allocation. This module owns all DCA_GRID-specific logic — main.py and the
dispatcher know nothing about grid mechanics.

Expected strategy metadata (base.trade_strategies.metadata):
{
    "capital_per_cycle":     1000,      -- Quote currency to deploy per cycle
    "num_levels":            5,         -- Number of grid buy levels
    "weights":               [1,1,2,3,3], -- Capital weight per level (deeper = more)
    "atr_multiplier_low":    0.4,       -- Grid spacing multiplier in low volatility
    "atr_multiplier_normal": 0.5,       -- Grid spacing multiplier in normal volatility
    "atr_multiplier_high":   0.7,       -- Grid spacing multiplier in high volatility
    "profit_target_low":     1.0,       -- Take-profit % in low volatility
    "profit_target_normal":  1.5,       -- Take-profit % in normal volatility
    "profit_target_high":    2.5,       -- Take-profit % in high volatility
    "max_atr_pct_entry":     6.0,       -- Skip entry if ATR% exceeds this
    "rsi_entry_max":         60,        -- Skip entry if RSI(14) exceeds this
    "reserve_capital_pct":   30         -- % of balance kept in reserve (not deployed)
}
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from common.connections.base import BaseExchangeConnection
from common.db import get_conn

from .base import BaseStrategy
from trader_bot.hybrid_coordinator import (
    REGIME_GRID_PAUSE,
    get_regime_score_with_circuit_breaker,
    grid_capital_limit,
    trend_has_priority,
)

logger = logging.getLogger(__name__)

# Volatility regime → ATR multiplier and profit target defaults
_REGIME_DEFAULTS = {
    "low":    {"multiplier_key": "atr_multiplier_low",    "profit_key": "profit_target_low",    "multiplier": 0.4, "profit": 1.3},
    "normal": {"multiplier_key": "atr_multiplier_normal", "profit_key": "profit_target_normal", "multiplier": 0.5, "profit": 1.8},
    "high":   {"multiplier_key": "atr_multiplier_high",   "profit_key": "profit_target_high",   "multiplier": 0.7, "profit": 3.0},
}


class DcaGridStrategy(BaseStrategy):

    strategy_type = "DCA_GRID"

    # ── Main tick ──────────────────────────────────────────────────────────────

    async def process(self, exchange: BaseExchangeConnection, strategy: dict) -> None:
        strategy_id = strategy["id"]

        async with get_conn() as conn:
            # Resolve any CLOSING cycles first. If a cycle finishes closing this
            # tick, the OPEN cycle check below will find nothing and
            # _maybe_open_cycle can start fresh on the next tick.
            await self._poll_closing_cycles(exchange, conn, strategy)

            cycle = await conn.fetchrow(
                """
                SELECT tc.*, dd.profit_target_pct, dd.grid_spacing_pct, dd.atr_at_open,
                       dd.volatility_regime, dd.atr_multiplier AS current_multiplier
                FROM base.trade_cycles tc
                LEFT JOIN base.trade_dca_cycle_details dd ON dd.cycle_id = tc.id
                WHERE tc.strategy_id = $1 AND tc.status = 'OPEN'
                ORDER BY tc.cycle_number DESC LIMIT 1
                """,
                strategy_id,
            )

            if not cycle:
                await self._maybe_open_cycle(exchange, conn, strategy)
                return

            cycle_id = cycle["id"]

            # Poll exchange for fills FIRST — updates trade_grid_levels,
            # trade_orders, and trade_executions so avg_entry is current
            # before we evaluate take-profit / stop-loss.
            await self._poll_open_orders(exchange, conn, strategy, cycle)

            price_row = await conn.fetchrow(
                """
                SELECT observed_price FROM base.price_observations
                WHERE base_asset_id = $1 AND quote_asset_id = $2
                ORDER BY observed_at DESC LIMIT 1
                """,
                strategy["base_asset_id"],
                strategy["quote_asset_id"],
            )
            if not price_row:
                logger.warning("Strategy %d: no price data, skipping tick.", strategy_id)
                return

            current_price = Decimal(str(price_row["observed_price"]))

            levels = await conn.fetch(
                """
                SELECT * FROM base.trade_grid_levels
                WHERE cycle_id = $1
                ORDER BY level_num ASC
                """,
                cycle_id,
            )

            # Auto-tune: if volatility regime changed, re-price pending levels
            # and update details table. Returns new profit_target_pct if retuned.
            retuned_profit_target = await self._maybe_retune(
                conn, strategy, cycle, current_price, list(levels),
            )

            meta = strategy["metadata"] or {}
            if retuned_profit_target is not None:
                effective_take_profit = retuned_profit_target / 100
            else:
                effective_take_profit = (
                    Decimal(str(cycle["profit_target_pct"]))
                    if cycle["profit_target_pct"] is not None
                    else Decimal(str(meta.get("profit_target_normal", "1.5")))
                ) / 100  # stored as percent, convert to decimal

            filled_levels  = [l for l in levels if l["status"] == "FILLED"]
            pending_levels = [l for l in levels if l["status"] == "PENDING"]
            open_levels    = [l for l in levels if l["status"] == "OPEN"]

            avg_entry = self._avg_entry(filled_levels)

            logger.info(
                "Strategy %d | cycle %d | price=%.6f | avg_entry=%.6f | "
                "levels filled=%d open=%d pending=%d",
                strategy_id, cycle_id, current_price, avg_entry,
                len(filled_levels), len(open_levels), len(pending_levels),
            )

            # ── Circuit breaker: extreme volatility → emergency close ──────────
            if await self._check_circuit_breaker(
                exchange, conn, strategy, cycle, filled_levels, current_price,
            ):
                return

            # ── Check take-profit ──────────────────────────────────────────────
            if avg_entry > 0 and filled_levels:
                target_sell = avg_entry * (
                    1 + effective_take_profit + Decimal(str(strategy["taker_fee_pct"]))
                )
                if current_price >= target_sell:
                    logger.info(
                        "Strategy %d: take-profit triggered — price=%.6f target=%.6f",
                        strategy_id, current_price, target_sell,
                    )
                    await self._trigger_take_profit(exchange, conn, strategy, cycle, filled_levels)
                    return

            # ── Check stop-loss ────────────────────────────────────────────────
            if cycle["stop_loss_price"] and current_price <= Decimal(str(cycle["stop_loss_price"])):
                logger.warning(
                    "Strategy %d: stop-loss triggered — price=%.6f stop=%.6f",
                    strategy_id, current_price, cycle["stop_loss_price"],
                )
                await self._trigger_stop_loss(exchange, conn, strategy, cycle, filled_levels)
                return

            # ── Place next pending level if price has reached it ───────────────
            if pending_levels:
                next_level = pending_levels[0]
                if current_price <= Decimal(str(next_level["target_price"])):
                    await self._place_grid_buy(exchange, conn, strategy, cycle, next_level)
                return

            # ── No pending levels: try crash grid expansion ────────────────────
            if filled_levels:
                await self._maybe_expand_grid(
                    conn, strategy, cycle, current_price, list(levels),
                )

    # ── Cycle opening ──────────────────────────────────────────────────────────

    async def _maybe_open_cycle(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
    ) -> None:
        """Evaluate entry conditions and open a new cycle if all pass."""
        strategy_id = strategy["id"]
        meta = strategy["metadata"] or {}

        # ── 1. Load latest indicators ──────────────────────────────────────────
        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators:
            logger.info(
                "Strategy %d: no indicator data available, cannot evaluate entry.",
                strategy_id,
            )
            return

        volatility_regime = indicators["volatility_regime"]
        atr_14            = Decimal(str(indicators["atr_14"]))
        atr_pct           = Decimal(str(indicators["atr_pct"]))    # ATR as % of price
        sma_50            = indicators["sma_50"]
        sma_200           = indicators["sma_200"]
        rsi_14            = indicators["rsi_14"]

        # ── 1b. Hybrid regime check ────────────────────────────────────────────
        # Fetch regime score (0.0 if intraday circuit breaker fires).
        # When no regime data exists yet (fresh install), fall through to normal
        # DCA logic — the grid runs unconstrained until regime data is available.
        regime_score = await get_regime_score_with_circuit_breaker(
            conn,
            asset_id       = strategy["base_asset_id"],
            base_asset_id  = strategy["base_asset_id"],
            quote_asset_id = strategy["quote_asset_id"],
        )

        if regime_score is not None:
            # RS >= 61 → strong trend, grid is paused
            if regime_score >= REGIME_GRID_PAUSE:
                logger.info(
                    "Strategy %d: RS=%.1f >= %.0f — grid paused in trending market.",
                    strategy_id, regime_score, REGIME_GRID_PAUSE,
                )
                return

            # RS > 50 + active trend cycle → trend has execution priority
            if await trend_has_priority(conn, strategy["base_asset_id"], regime_score):
                logger.info(
                    "Strategy %d: RS=%.1f > 50 and TREND_FOLLOW cycle is open "
                    "— deferring grid entry.",
                    strategy_id, regime_score,
                )
                return

        # ── 2. Load current price ──────────────────────────────────────────────
        price_row = await conn.fetchrow(
            """
            SELECT observed_price FROM base.price_observations
            WHERE base_asset_id = $1 AND quote_asset_id = $2
            ORDER BY observed_at DESC LIMIT 1
            """,
            strategy["base_asset_id"],
            strategy["quote_asset_id"],
        )
        if not price_row:
            logger.info("Strategy %d: no price data, cannot evaluate entry.", strategy_id)
            return

        current_price = Decimal(str(price_row["observed_price"]))

        # ── 3. Entry filter ────────────────────────────────────────────────────
        active_meta = meta   # may be swapped to defensive meta below

        if meta.get("force_entry"):
            logger.warning(
                "Strategy %d: force_entry=true — bypassing all entry conditions.", strategy_id
            )
        else:
            passed, reason = self._check_entry_conditions(
                meta, volatility_regime, atr_pct, current_price, sma_50, sma_200, rsi_14,
            )
            if not passed:
                # Try defensive grid if the strategy is configured for it
                if meta.get("defensive_mode_enabled"):
                    symbol = (
                        f"{strategy['base_asset_code'].upper()}"
                        f"/{strategy['quote_asset_code'].upper()}"
                    )
                    intraday_rsi = await self._load_intraday_rsi(
                        exchange = exchange,
                        symbol   = symbol,
                        period   = int(meta.get("defensive_rsi_period", 14)),
                        timeframe = meta.get("defensive_rsi_timeframe", "1h"),
                    )
                    bounce, bounce_reason = self._check_defensive_entry(
                        meta, indicators, current_price, intraday_rsi=intraday_rsi,
                    )
                    if bounce:
                        active_meta = self._build_defensive_meta(meta)
                        logger.info(
                            "Strategy %d: defensive grid entry — %s", strategy_id, bounce_reason
                        )
                    else:
                        logger.info(
                            "Strategy %d: downtrend, no bounce signal — %s", strategy_id, bounce_reason
                        )
                        return
                else:
                    logger.info("Strategy %d: entry conditions not met — %s", strategy_id, reason)
                    return

        # ── 4. Capital check ───────────────────────────────────────────────────
        capital_per_cycle = Decimal(str(active_meta.get("capital_per_cycle", 1000)))
        reserve_pct       = Decimal(str(active_meta.get("reserve_capital_pct", 30))) / 100

        # Regime-scale the capital: lower RS → grid keeps more of its allocation.
        # Applied only at cycle-open time; existing cycles are never resized.
        if regime_score is not None:
            capital_per_cycle = grid_capital_limit(capital_per_cycle, regime_score)
            logger.debug(
                "Strategy %d: regime-scaled grid capital: %.2f (RS=%.1f)",
                strategy_id, capital_per_cycle, regime_score,
            )

        available = await self._available_capital(exchange, conn, strategy)
        if available is None:
            logger.warning("Strategy %d: could not determine available capital.", strategy_id)
            return

        # Honour reserve: bot may only deploy (1 - reserve_pct) of available balance
        deployable = available * (1 - reserve_pct)
        if deployable < capital_per_cycle:
            logger.info(
                "Strategy %d: insufficient capital — deployable=%.2f required=%.2f",
                strategy_id, deployable, capital_per_cycle,
            )
            return

        # ── 5. Compute grid parameters ─────────────────────────────────────────
        regime_cfg   = _REGIME_DEFAULTS.get(volatility_regime, _REGIME_DEFAULTS["normal"])
        multiplier   = Decimal(str(active_meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        profit_target_pct = Decimal(str(active_meta.get(regime_cfg["profit_key"], regime_cfg["profit"])))

        # grid_spacing_pct: ATR% * multiplier (e.g. 3% ATR * 0.5 = 1.5% spacing)
        grid_spacing_pct = atr_pct * multiplier

        num_levels = int(active_meta.get("num_levels", 5))
        weights    = active_meta.get("weights", [1] * num_levels)
        if len(weights) != num_levels:
            weights = [1] * num_levels  # safety fallback

        grid_levels = self._compute_grid_levels(
            reference_price   = current_price,
            grid_spacing_pct  = grid_spacing_pct,
            num_levels        = num_levels,
            weights           = weights,
            capital_per_cycle = capital_per_cycle,
            atr_value         = atr_14,
            atr_multiplier    = multiplier,
            maker_fee_pct     = Decimal(str(strategy.get("maker_fee_pct", 0))),
        )

        # Stop-loss: one spacing below the deepest grid level
        deepest_price   = grid_levels[-1]["target_price"]
        stop_loss_price = deepest_price * (1 - grid_spacing_pct / 100)

        # ── 6. Atomic DB write ─────────────────────────────────────────────────
        await self._open_cycle(
            conn             = conn,
            strategy         = strategy,
            current_price    = current_price,
            capital_per_cycle = capital_per_cycle,
            stop_loss_price  = stop_loss_price,
            atr_at_open      = atr_14,
            atr_multiplier   = multiplier,
            grid_spacing_pct = grid_spacing_pct,
            profit_target_pct = profit_target_pct,
            volatility_regime = volatility_regime,
            grid_levels      = grid_levels,
        )

    def _check_entry_conditions(
        self,
        meta: dict,
        volatility_regime: str,
        atr_pct: Decimal,
        current_price: Decimal,
        sma_50,
        sma_200,
        rsi_14,
    ) -> tuple[bool, str]:
        """
        Validate all entry conditions.
        Returns (passed: bool, reason: str).
        """
        max_atr_pct        = Decimal(str(meta.get("max_atr_pct_entry", 6.0)))
        rsi_entry_max      = Decimal(str(meta.get("rsi_entry_max", 60)))
        require_uptrend    = meta.get("require_uptrend", True)     # price > SMA200
        require_golden_cross = meta.get("require_golden_cross", True)  # SMA50 > SMA200

        if volatility_regime in ("high", "extreme"):
            return False, f"volatility_regime={volatility_regime} — pausing"

        if atr_pct >= max_atr_pct:
            return False, f"atr_pct={atr_pct:.2f}% >= max {max_atr_pct}%"

        if require_uptrend and sma_200 is not None and current_price < Decimal(str(sma_200)):
            return False, f"price={current_price:.2f} < sma_200={sma_200:.2f} (downtrend)"

        # Death cross: SMA50 below SMA200 signals a sustained downtrend — avoid new entries.
        if require_golden_cross and sma_50 is not None and sma_200 is not None:
            if Decimal(str(sma_50)) < Decimal(str(sma_200)):
                return False, f"death cross: sma_50={float(sma_50):.2f} < sma_200={float(sma_200):.2f}"

        if rsi_14 is not None and Decimal(str(rsi_14)) >= rsi_entry_max:
            return False, f"rsi_14={rsi_14:.2f} >= {rsi_entry_max} (extended)"

        return True, "all conditions passed"

    def _check_defensive_entry(
        self,
        meta:          dict,
        indicators:    dict,
        current_price: Decimal,
        intraday_rsi:  float | None = None,
    ) -> tuple[bool, str]:
        """
        Confirm we are in a downtrend AND a bounce signal is present.
        Called only when normal entry conditions fail and defensive_mode_enabled=True.

        Uses intraday_rsi (1h) when available, falls back to daily rsi_14.
        Bounce signal: RSI < defensive_rsi_oversold.
        """
        sma_200 = indicators.get("sma_200")
        sma_50  = indicators.get("sma_50")

        # Must actually be in a downtrend to use defensive mode
        in_downtrend = (
            (sma_200 is not None and current_price < Decimal(str(sma_200)))
            or (sma_50 is not None and sma_200 is not None
                and Decimal(str(sma_50)) < Decimal(str(sma_200)))
        )
        if not in_downtrend:
            return False, "not in downtrend — defensive mode not applicable"

        # Prefer intraday RSI (more responsive), fall back to daily
        if intraday_rsi is not None:
            rsi_value  = intraday_rsi
            rsi_source = f"{meta.get('defensive_rsi_timeframe', '1h')}"
        else:
            rsi_value  = indicators.get("rsi_14")
            rsi_source = "1d"

        rsi_threshold = Decimal(str(meta.get("defensive_rsi_oversold", 40)))
        if rsi_value is not None and Decimal(str(rsi_value)) < rsi_threshold:
            return True, (
                f"oversold bounce: rsi({rsi_source})={float(rsi_value):.1f} < {rsi_threshold}"
            )

        rsi_str = f"{float(rsi_value):.1f}" if rsi_value is not None else "n/a"
        return False, (
            f"downtrend confirmed but no bounce signal "
            f"(rsi({rsi_source})={rsi_str} >= {rsi_threshold})"
        )

    def _build_defensive_meta(self, meta: dict) -> dict:
        """
        Return a copy of meta with normal/low/high grid params overridden by
        defensive values — wider spacing, higher profit target, fewer levels.
        The strategy's DB record is NOT modified; this only affects this cycle open.
        """
        d_mult   = meta.get("defensive_atr_multiplier", 0.8)
        d_profit = meta.get("defensive_profit_target",  2.5)
        d_levels = int(meta.get("defensive_num_levels", 5))

        overrides = {
            # Override all three regime multiplier keys so whichever regime is
            # active at open time uses the defensive (wider) spacing.
            "atr_multiplier_low":    d_mult,
            "atr_multiplier_normal": d_mult,
            "atr_multiplier_high":   d_mult,
            # Override all three regime profit keys.
            "profit_target_low":    d_profit,
            "profit_target_normal": d_profit,
            "profit_target_high":   d_profit,
            # Fewer, equal-weight levels for a simpler defensive grid.
            "num_levels": d_levels,
            "weights":    [1] * d_levels,
        }
        return {**meta, **overrides}

    async def _load_intraday_rsi(
        self,
        exchange,
        symbol:    str,
        period:    int = 14,
        timeframe: str = "1h",
    ) -> float | None:
        """
        Fetch recent candles from the exchange and compute RSI.
        Returns None if data is unavailable or insufficient.
        Uses (period * 2 + 1) candles so Wilder smoothing is well-seeded.
        """
        try:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=period * 2 + 1
            )
            if not candles or len(candles) < period + 1:
                logger.debug(
                    "Intraday RSI: insufficient candles (%d) for period %d",
                    len(candles) if candles else 0, period,
                )
                return None
            closes = [c["close"] for c in candles]
            rsi    = self._compute_rsi(closes, period)
            logger.debug(
                "Intraday RSI(%d, %s): %.2f  [%d candles]", period, timeframe, rsi, len(closes)
            )
            return rsi
        except Exception as exc:
            logger.warning("Could not fetch intraday OHLCV for RSI (%s): %s", symbol, exc)
            return None

    @staticmethod
    def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
        """
        RSI using Wilder's smoothing (identical to TradingView / pandas-ta default).

        Requires at least period+1 values (to compute period price changes).
        Returns None if there is insufficient data.
        """
        if len(closes) < period + 1:
            return None

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [max(d, 0.0) for d in deltas]
        losses = [abs(min(d, 0.0)) for d in deltas]

        # Seed: simple average of first `period` changes
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Wilder smoothing over remaining changes
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1 + rs)), 2)

    def _compute_grid_levels(
        self,
        reference_price: Decimal,
        grid_spacing_pct: Decimal,    # e.g. Decimal("1.5") means 1.5%
        num_levels: int,
        weights: list,
        capital_per_cycle: Decimal,
        atr_value: Decimal,
        atr_multiplier: Decimal,
        maker_fee_pct: Decimal = Decimal("0"),
    ) -> list[dict]:
        """
        Compute grid level parameters.

        Levels are spaced below reference_price using grid_spacing_pct.
        Capital is distributed across levels proportional to weights (deeper = more).
        maker_fee_pct is deducted from quantity so the allocated capital covers the fee.

        Returns a list of level dicts ready to insert into trade_grid_levels.
        """
        total_weight = sum(weights)
        spacing      = grid_spacing_pct / 100  # convert % to decimal multiplier

        levels = []
        for i, weight in enumerate(weights, start=1):
            target_price      = reference_price * (1 - i * spacing)
            capital_allocated = capital_per_cycle * Decimal(weight) / Decimal(total_weight)
            quantity          = capital_allocated / (target_price * (1 + maker_fee_pct))

            levels.append({
                "level_num":        i,
                "target_price":     target_price,
                "weight":           Decimal(weight),
                "capital_allocated": capital_allocated,
                "quantity":         quantity,
                "atr_value":        atr_value,
                "atr_multiplier":   atr_multiplier,
                "level_trigger":    "initial",
            })

        return levels

    async def _open_cycle(
        self,
        conn,
        strategy: dict,
        current_price: Decimal,
        capital_per_cycle: Decimal,
        stop_loss_price: Decimal,
        atr_at_open: Decimal,
        atr_multiplier: Decimal,
        grid_spacing_pct: Decimal,
        profit_target_pct: Decimal,
        volatility_regime: str,
        grid_levels: list[dict],
    ) -> None:
        """Write a new cycle and all associated records in a single transaction."""
        strategy_id = strategy["id"]

        async with conn.transaction():
            # Next cycle number for this strategy
            cycle_number = await conn.fetchval(
                """
                SELECT COALESCE(MAX(cycle_number), 0) + 1
                FROM base.trade_cycles
                WHERE strategy_id = $1
                """,
                strategy_id,
            )

            # 1. trade_cycles
            cycle_id = await conn.fetchval(
                """
                INSERT INTO base.trade_cycles
                    (strategy_id, cycle_number, capital_allocated, status,
                     stop_loss_price, opened_at)
                VALUES ($1, $2, $3, 'OPEN', $4, NOW())
                RETURNING id
                """,
                strategy_id, cycle_number,
                float(capital_per_cycle), float(stop_loss_price),
            )

            # 2. trade_dca_cycle_details
            await conn.execute(
                """
                INSERT INTO base.trade_dca_cycle_details
                    (cycle_id, strategy_id, atr_at_open, atr_multiplier,
                     grid_spacing_pct, profit_target_pct, volatility_regime, last_tuned_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                """,
                cycle_id, strategy_id,
                float(atr_at_open), float(atr_multiplier),
                float(grid_spacing_pct), float(profit_target_pct),
                volatility_regime,
            )

            # 3. trade_grid_levels (batch insert)
            await conn.executemany(
                """
                INSERT INTO base.trade_grid_levels
                    (cycle_id, strategy_id, level_num, target_price, weight,
                     capital_allocated, quantity, atr_value, atr_multiplier,
                     level_trigger, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'PENDING')
                """,
                [
                    (
                        cycle_id, strategy_id,
                        lvl["level_num"],
                        float(lvl["target_price"]),
                        float(lvl["weight"]),
                        float(lvl["capital_allocated"]),
                        float(lvl["quantity"]),
                        float(lvl["atr_value"]),
                        float(lvl["atr_multiplier"]),
                        lvl["level_trigger"],
                    )
                    for lvl in grid_levels
                ],
            )

            # 4. capital_locks
            await conn.execute(
                """
                INSERT INTO base.capital_locks
                    (venue_id, asset_id, cycle_id, strategy_id, amount, status, locked_at)
                VALUES ($1, $2, $3, $4, $5, 'ACTIVE', NOW())
                """,
                strategy["venue_id"], strategy["quote_asset_id"],
                cycle_id, strategy_id, float(capital_per_cycle),
            )

            # 5. system_events
            await conn.execute(
                """
                INSERT INTO base.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'CYCLE_OPENED', 'INFO', $1, $2, $3, $4)
                """,
                strategy_id, cycle_id,
                f"Opened DCA grid cycle #{cycle_number} for strategy {strategy_id}",
                json.dumps({
                    "cycle_number":     cycle_number,
                    "reference_price":  float(current_price),
                    "capital":          float(capital_per_cycle),
                    "stop_loss_price":  float(stop_loss_price),
                    "atr_at_open":      float(atr_at_open),
                    "atr_multiplier":   float(atr_multiplier),
                    "grid_spacing_pct": float(grid_spacing_pct),
                    "profit_target_pct": float(profit_target_pct),
                    "volatility_regime": volatility_regime,
                    "num_levels":       len(grid_levels),
                }),
            )

        logger.info(
            "Strategy %d: opened cycle #%d | price=%.2f | spacing=%.2f%% | "
            "profit_target=%.2f%% | stop_loss=%.2f | levels=%d",
            strategy_id, cycle_number, current_price,
            grid_spacing_pct, profit_target_pct, stop_loss_price, len(grid_levels),
        )

    # ── Capital availability ───────────────────────────────────────────────────

    async def _available_capital(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
    ) -> Decimal | None:
        """
        Return available quote-asset capital for this strategy's venue.

        Checks venue_available_capital view first (real mode).
        Falls back to exchange.fetch_balance() when no venue_balances row exists
        (paper mode / balance not yet synced).
        """
        row = await conn.fetchrow(
            """
            SELECT available_balance
            FROM base.venue_available_capital
            WHERE venue_id = $1 AND asset_id = $2
            """,
            strategy["venue_id"], strategy["quote_asset_id"],
        )
        if row is not None:
            return Decimal(str(row["available_balance"]))

        # Fallback: ask the exchange directly (works for paper mode)
        try:
            balance = await exchange.fetch_balance()
            quote_code = strategy["quote_asset_code"].upper()
            free = balance.get(quote_code, {}).get("free", 0)
            return Decimal(str(free))
        except Exception as exc:
            logger.warning("Strategy %d: fetch_balance failed: %s", strategy["id"], exc)
            return None

    # ── Indicator loading ──────────────────────────────────────────────────────

    async def _load_latest_indicators(self, conn, asset_id: int) -> dict | None:
        """
        Load the most recent asset_indicators_1d row for the given asset.
        Returns None if no data is available or required fields are NULL.
        """
        row = await conn.fetchrow(
            """
            SELECT atr_14, atr_pct, atr_sma_20, volatility_regime,
                   sma_50, sma_200, rsi_14, metric_date
            FROM base.asset_indicators_1d
            WHERE asset_id = $1
              AND atr_14 IS NOT NULL
              AND atr_pct IS NOT NULL
              AND volatility_regime IS NOT NULL
            ORDER BY metric_date DESC
            LIMIT 1
            """,
            asset_id,
        )
        return dict(row) if row else None

    # ── Fill polling ───────────────────────────────────────────────────────────

    async def _poll_open_orders(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
    ) -> None:
        """
        Check every OPEN grid level against the exchange for fill status.

        For each level whose order is now closed (filled) on the exchange,
        calls _record_fill() to persist the execution and advance the level
        status to FILLED.

        Errors on individual orders are logged and skipped — a single failed
        fetch_order call must not abort the whole poll cycle.
        """
        cycle_id    = cycle["id"]
        strategy_id = strategy["id"]
        symbol      = f"{strategy['base_asset_code']}/{strategy['quote_asset_code']}"

        open_levels = await conn.fetch(
            """
            SELECT gl.*, tor.exchange_order_id, tor.id AS trade_order_id
            FROM base.trade_grid_levels gl
            JOIN base.trade_orders tor ON tor.id = gl.order_id
            WHERE gl.cycle_id = $1 AND gl.status = 'OPEN'
            ORDER BY gl.level_num ASC
            """,
            cycle_id,
        )

        if not open_levels:
            return

        for level in open_levels:
            exchange_order_id = level["exchange_order_id"]
            try:
                order_status = await exchange.fetch_order(exchange_order_id, symbol)
            except Exception as exc:
                logger.warning(
                    "Strategy %d | level %d: fetch_order failed for %s: %s",
                    strategy_id, level["level_num"], exchange_order_id, exc,
                )
                continue

            if order_status.get("status") == "closed":
                await self._record_fill(conn, strategy, cycle, level, order_status)

    async def _record_fill(
        self,
        conn,
        strategy: dict,
        cycle: dict,
        level: dict,
        order_status: dict,
    ) -> None:
        """
        Persist a confirmed fill in a single transaction:
          1. INSERT trade_executions (immutable fill record)
          2. UPDATE trade_orders     (filled_quantity, avg_fill_price, fee, status)
          3. UPDATE trade_grid_levels (status → FILLED, filled_at)

        ON CONFLICT DO NOTHING on trade_executions guards against duplicate
        processing if the same filled order is polled more than once.
        """
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]
        order_id    = level["trade_order_id"]

        # Prefer exchange-reported fill price; fall back to the level's target price.
        executed_price = Decimal(str(
            order_status.get("average") or
            order_status.get("price") or
            level["target_price"]
        ))
        executed_qty = Decimal(str(order_status.get("filled") or level["quantity"]))
        quote_qty    = executed_price * executed_qty

        fee      = order_status.get("fee") or {}
        fee_cost = Decimal(str(fee.get("cost") or 0))
        fee_asset = fee.get("currency")

        # For real ccxt exchanges, use the first trade ID if available.
        # For paper mode (trades=[]), synthesise a deterministic execution ID.
        trades = order_status.get("trades") or []
        exchange_execution_id = (
            str(trades[0]["id"])
            if trades
            else f"{level['exchange_order_id']}_fill"
        )

        async with conn.transaction():
            # 1. trade_executions
            await conn.execute(
                """
                INSERT INTO base.trade_executions
                    (order_id, cycle_id, exchange_execution_id, side,
                     executed_price, executed_quantity, quote_quantity,
                     fee_amount, fee_asset, executed_at)
                VALUES ($1, $2, $3, 'BUY', $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (order_id, exchange_execution_id) DO NOTHING
                """,
                order_id, cycle_id, exchange_execution_id,
                float(executed_price), float(executed_qty), float(quote_qty),
                float(fee_cost), fee_asset,
            )

            # 2. trade_orders
            await conn.execute(
                """
                UPDATE base.trade_orders
                SET status          = 'FILLED',
                    filled_quantity = $1,
                    avg_fill_price  = $2,
                    fee_total       = $3,
                    fee_asset       = $4,
                    updated_at      = NOW()
                WHERE id = $5
                """,
                float(executed_qty), float(executed_price),
                float(fee_cost), fee_asset, order_id,
            )

            # 3. trade_grid_levels
            await conn.execute(
                """
                UPDATE base.trade_grid_levels
                SET status = 'FILLED', filled_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                level["id"],
            )

        logger.info(
            "Strategy %d | level %d FILLED: qty=%.8f @ %.6f (fee=%.4f %s)",
            strategy_id, level["level_num"],
            executed_qty, executed_price, fee_cost, fee_asset or "",
        )

    # ── Order placement ────────────────────────────────────────────────────────

    async def _place_grid_buy(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
        level: dict,
    ) -> None:
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]
        symbol      = f"{strategy['base_asset_code']}/{strategy['quote_asset_code']}"
        price       = Decimal(str(level["target_price"]))
        quantity    = Decimal(str(level["quantity"]))

        logger.info(
            "Strategy %d | placing grid buy level %d: qty=%.8f @ %.6f",
            strategy_id, level["level_num"], quantity, price,
        )

        try:
            order_result = await exchange.create_order(
                symbol, side="buy", order_type="limit",
                amount=float(quantity), price=float(price),
            )
            exchange_order_id = str(order_result["id"])
        except Exception as exc:
            logger.error("Strategy %d: failed to place buy order: %s", strategy_id, exc)
            return

        # Record in trade_orders and link grid level in a transaction
        async with conn.transaction():
            order_id = await conn.fetchval(
                """
                INSERT INTO base.trade_orders
                    (cycle_id, strategy_id, exchange_order_id, side, order_type,
                     target_price, quantity, status, submitted_at)
                VALUES ($1, $2, $3, 'BUY', 'LIMIT', $4, $5, 'OPEN', NOW())
                RETURNING id
                """,
                cycle_id, strategy_id, exchange_order_id,
                float(price), float(quantity),
            )

            await conn.execute(
                """
                UPDATE base.trade_grid_levels
                SET status = 'OPEN', order_id = $1, updated_at = NOW()
                WHERE id = $2
                """,
                order_id, level["id"],
            )

    # ── Cycle closing ──────────────────────────────────────────────────────────

    async def _trigger_take_profit(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
        filled_levels: list,
    ) -> None:
        await self._close_cycle(
            exchange, conn, strategy, cycle, filled_levels, trigger="take_profit",
        )

    async def _trigger_stop_loss(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
        filled_levels: list,
    ) -> None:
        await self._close_cycle(
            exchange, conn, strategy, cycle, filled_levels, trigger="stop_loss",
        )

    async def _close_cycle(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
        filled_levels: list,
        trigger: str,
    ) -> None:
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]
        symbol      = f"{strategy['base_asset_code']}/{strategy['quote_asset_code']}"
        total_qty   = sum(Decimal(str(l["quantity"])) for l in filled_levels)

        if total_qty > 0:
            try:
                order_result = await exchange.create_order(
                    symbol, side="sell", order_type="market", amount=float(total_qty),
                )
                exchange_order_id = str(order_result["id"])
                await conn.execute(
                    """
                    INSERT INTO base.trade_orders
                        (cycle_id, strategy_id, exchange_order_id, side, order_type,
                         quantity, status, submitted_at)
                    VALUES ($1, $2, $3, 'SELL', 'MARKET', $4, 'OPEN', NOW())
                    """,
                    cycle_id, strategy_id, exchange_order_id, float(total_qty),
                )
            except Exception as exc:
                logger.error(
                    "Strategy %d: failed to place %s sell: %s", strategy_id, trigger, exc,
                )

        await conn.execute(
            """
            UPDATE base.trade_cycles
            SET status = 'CLOSING', close_trigger = $1, updated_at = NOW()
            WHERE id = $2
            """,
            trigger, cycle_id,
        )

        logger.info(
            "Strategy %d | cycle %d: moving to CLOSING (%s). qty=%.8f",
            strategy_id, cycle_id, trigger, total_qty,
        )

    # ── Cycle closer ───────────────────────────────────────────────────────────

    async def _poll_closing_cycles(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
    ) -> None:
        """
        Check every CLOSING cycle to see if its sell order has filled.

        A cycle enters CLOSING when take-profit or stop-loss fires and a sell
        order is submitted. This method confirms the fill on the exchange and,
        if confirmed, calls _finalize_closed_cycle() to write PnL and mark the
        cycle CLOSED.
        """
        strategy_id = strategy["id"]
        symbol      = f"{strategy['base_asset_code']}/{strategy['quote_asset_code']}"

        closing_cycles = await conn.fetch(
            """
            SELECT * FROM base.trade_cycles
            WHERE strategy_id = $1 AND status = 'CLOSING'
            ORDER BY cycle_number ASC
            """,
            strategy_id,
        )

        for cycle in closing_cycles:
            cycle_id = cycle["id"]

            sell_order = await conn.fetchrow(
                """
                SELECT * FROM base.trade_orders
                WHERE cycle_id = $1 AND side = 'SELL' AND status = 'OPEN'
                ORDER BY submitted_at DESC LIMIT 1
                """,
                cycle_id,
            )

            if not sell_order:
                logger.warning(
                    "Strategy %d | cycle %d: CLOSING but no open sell order found.",
                    strategy_id, cycle_id,
                )
                continue

            try:
                order_status = await exchange.fetch_order(
                    sell_order["exchange_order_id"], symbol,
                )
            except Exception as exc:
                logger.warning(
                    "Strategy %d | cycle %d: fetch_order for sell failed: %s",
                    strategy_id, cycle_id, exc,
                )
                continue

            if order_status.get("status") == "closed":
                await self._finalize_closed_cycle(
                    conn, strategy, cycle, sell_order, order_status,
                )

    async def _finalize_closed_cycle(
        self,
        conn,
        strategy: dict,
        cycle: dict,
        sell_order: dict,
        order_status: dict,
    ) -> None:
        """
        Write all end-of-cycle records in a single transaction:
          1. INSERT trade_executions  (sell fill)
          2. UPDATE trade_orders      (sell → FILLED)
          3. UPDATE trade_grid_levels (remaining OPEN/PENDING → CANCELLED)
          4. INSERT trade_pnl
          5. UPDATE capital_locks     (ACTIVE → RELEASED)
          6. UPDATE trade_cycles      (CLOSING → CLOSED)
          7. INSERT system_events     (CYCLE_CLOSED)
        """
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]

        # ── Sell fill details ──────────────────────────────────────────────────
        sell_price = Decimal(str(
            order_status.get("average") or
            order_status.get("price") or 0
        ))
        sell_qty      = Decimal(str(order_status.get("filled") or sell_order["quantity"]))
        sell_proceeds = sell_price * sell_qty

        sell_fee      = order_status.get("fee") or {}
        sell_fee_cost = Decimal(str(sell_fee.get("cost") or 0))
        sell_fee_asset = sell_fee.get("currency")

        sell_trades = order_status.get("trades") or []
        sell_execution_id = (
            str(sell_trades[0]["id"])
            if sell_trades
            else f"{sell_order['exchange_order_id']}_fill"
        )

        # ── Buy side summary from trade_executions ─────────────────────────────
        buy_summary = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(executed_quantity), 0) AS total_qty,
                COALESCE(SUM(quote_quantity),    0) AS total_cost,
                COALESCE(SUM(fee_amount),        0) AS total_fees
            FROM base.trade_executions
            WHERE cycle_id = $1 AND side = 'BUY'
            """,
            cycle_id,
        )

        total_buy_qty  = Decimal(str(buy_summary["total_qty"]))
        total_buy_cost = Decimal(str(buy_summary["total_cost"]))
        total_buy_fees = Decimal(str(buy_summary["total_fees"]))
        avg_buy_price  = total_buy_cost / total_buy_qty if total_buy_qty > 0 else Decimal("0")

        # ── PnL ────────────────────────────────────────────────────────────────
        total_fees = total_buy_fees + sell_fee_cost
        gross_pnl  = sell_proceeds - total_buy_cost
        net_pnl    = gross_pnl - total_fees
        pnl_pct    = (net_pnl / total_buy_cost * 100) if total_buy_cost > 0 else Decimal("0")

        # ── Cycle duration ─────────────────────────────────────────────────────
        opened_at = cycle["opened_at"]
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        duration_seconds = int((datetime.now(timezone.utc) - opened_at).total_seconds())

        async with conn.transaction():
            # 1. trade_executions — sell fill
            await conn.execute(
                """
                INSERT INTO base.trade_executions
                    (order_id, cycle_id, exchange_execution_id, side,
                     executed_price, executed_quantity, quote_quantity,
                     fee_amount, fee_asset, executed_at)
                VALUES ($1, $2, $3, 'SELL', $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (order_id, exchange_execution_id) DO NOTHING
                """,
                sell_order["id"], cycle_id, sell_execution_id,
                float(sell_price), float(sell_qty), float(sell_proceeds),
                float(sell_fee_cost), sell_fee_asset,
            )

            # 2. trade_orders — mark sell as FILLED
            await conn.execute(
                """
                UPDATE base.trade_orders
                SET status          = 'FILLED',
                    filled_quantity = $1,
                    avg_fill_price  = $2,
                    fee_total       = $3,
                    fee_asset       = $4,
                    updated_at      = NOW()
                WHERE id = $5
                """,
                float(sell_qty), float(sell_price),
                float(sell_fee_cost), sell_fee_asset, sell_order["id"],
            )

            # 3. trade_grid_levels — cancel any unsettled levels
            await conn.execute(
                """
                UPDATE base.trade_grid_levels
                SET status = 'CANCELLED', updated_at = NOW()
                WHERE cycle_id = $1 AND status IN ('OPEN', 'PENDING')
                """,
                cycle_id,
            )

            # 4. trade_pnl
            await conn.execute(
                """
                INSERT INTO base.trade_pnl
                    (cycle_id, strategy_id,
                     total_buy_quantity, total_buy_cost, avg_buy_price,
                     total_sell_quantity, total_sell_proceeds, avg_sell_price,
                     total_fees, gross_pnl, net_pnl, pnl_pct,
                     cycle_duration_seconds, closed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
                """,
                cycle_id, strategy_id,
                float(total_buy_qty),  float(total_buy_cost),  float(avg_buy_price),
                float(sell_qty),       float(sell_proceeds),   float(sell_price),
                float(total_fees),     float(gross_pnl),       float(net_pnl),
                float(pnl_pct),        duration_seconds,
            )

            # 5. capital_locks — release
            await conn.execute(
                """
                UPDATE base.capital_locks
                SET status = 'RELEASED', released_at = NOW(), updated_at = NOW()
                WHERE cycle_id = $1 AND status = 'ACTIVE'
                """,
                cycle_id,
            )

            # 6. trade_cycles — mark CLOSED
            await conn.execute(
                """
                UPDATE base.trade_cycles
                SET status = 'CLOSED', closed_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                cycle_id,
            )

            # 7. system_events
            await conn.execute(
                """
                INSERT INTO base.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'CYCLE_CLOSED', 'INFO', $1, $2, $3, $4)
                """,
                strategy_id, cycle_id,
                (
                    f"Closed DCA grid cycle #{cycle['cycle_number']} "
                    f"({cycle['close_trigger']}) | "
                    f"net_pnl={float(net_pnl):.2f} ({float(pnl_pct):.4f}%)"
                ),
                json.dumps({
                    "close_trigger":       cycle["close_trigger"],
                    "avg_buy_price":       float(avg_buy_price),
                    "avg_sell_price":      float(sell_price),
                    "total_buy_cost":      float(total_buy_cost),
                    "total_sell_proceeds": float(sell_proceeds),
                    "gross_pnl":           float(gross_pnl),
                    "net_pnl":             float(net_pnl),
                    "pnl_pct":             float(pnl_pct),
                    "total_fees":          float(total_fees),
                    "duration_seconds":    duration_seconds,
                }),
            )

        logger.info(
            "Strategy %d | cycle %d CLOSED (%s) | "
            "buy_cost=%.2f sell_proceeds=%.2f gross=%.2f net=%.2f (%.4f%%) fees=%.4f",
            strategy_id, cycle_id, cycle["close_trigger"],
            total_buy_cost, sell_proceeds, gross_pnl, net_pnl, pnl_pct, total_fees,
        )

    # ── Crash protection ───────────────────────────────────────────────────────

    async def _check_circuit_breaker(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle: dict,
        filled_levels: list,
        current_price: Decimal,
    ) -> bool:
        """
        Emergency close an OPEN cycle when volatility spikes to 'extreme' or ATR%
        exceeds the configurable circuit-breaker threshold.

        Returns True if the breaker fired (caller should return immediately).
        When fired:
          - Logs a CIRCUIT_BREAKER_TRIGGERED system event.
          - If inventory exists (filled_levels), calls _close_cycle to submit a sell.
          - If no inventory, the cycle stays OPEN (nothing to sell); the log and
            return True prevent further order placement this tick.
        """
        meta = strategy["metadata"] or {}
        cb_threshold = Decimal(str(meta.get("circuit_breaker_atr_pct", 8.0)))

        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators:
            return False

        current_regime = indicators["volatility_regime"]
        atr_pct        = Decimal(str(indicators["atr_pct"]))

        triggered = current_regime == "extreme" or atr_pct >= cb_threshold
        if not triggered:
            return False

        logger.warning(
            "Strategy %d | cycle %d: CIRCUIT BREAKER triggered — "
            "regime=%s atr_pct=%.2f%% (threshold=%.2f%%)",
            strategy["id"], cycle["id"], current_regime, atr_pct, cb_threshold,
        )

        await conn.execute(
            """
            INSERT INTO base.system_events
                (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
            VALUES ('trader_bot', 'CIRCUIT_BREAKER_TRIGGERED', 'WARNING', $1, $2, $3, $4)
            """,
            strategy["id"], cycle["id"],
            (
                f"Circuit breaker triggered for cycle #{cycle['cycle_number']}: "
                f"regime={current_regime} atr_pct={float(atr_pct):.2f}%"
            ),
            json.dumps({
                "regime":        current_regime,
                "atr_pct":       float(atr_pct),
                "cb_threshold":  float(cb_threshold),
                "current_price": float(current_price),
                "filled_levels": len(filled_levels),
            }),
        )

        if filled_levels:
            await self._close_cycle(
                exchange, conn, strategy, cycle, filled_levels,
                trigger="circuit_breaker",
            )

        return True

    async def _maybe_expand_grid(
        self,
        conn,
        strategy: dict,
        cycle: dict,
        current_price: Decimal,
        levels: list,
    ) -> bool:
        """
        Add deeper crash-expansion grid levels when all pending levels are consumed
        but the cycle is still above the stop-loss.

        Triggered when:
          - No PENDING levels remain
          - At least one FILLED level exists (inventory deployed)
          - Current price is above stop_loss_price
          - Expansion count is below max_expansions (default 1)

        New levels use high-volatility multiplier for wider spacing and a fraction
        of the original cycle capital (smaller orders during a crash).

        Returns True if expansion levels were added.
        """
        meta = strategy["metadata"] or {}
        max_expansions          = int(meta.get("max_expansions", 1))
        expansion_levels_count  = int(meta.get("expansion_levels", 2))
        expansion_capital_frac  = Decimal(str(meta.get("expansion_capital_fraction", 0.3)))

        pending_count = sum(1 for l in levels if l["status"] == "PENDING")
        filled_count  = sum(1 for l in levels if l["status"] == "FILLED")

        if pending_count > 0:
            return False
        if filled_count == 0:
            return False
        if cycle["stop_loss_price"] and current_price <= Decimal(str(cycle["stop_loss_price"])):
            return False

        # Count crash-expansion levels already added
        crash_count   = sum(1 for l in levels if l.get("level_trigger") == "crash_expansion")
        expansions_done = crash_count // expansion_levels_count if expansion_levels_count > 0 else 0
        if expansions_done >= max_expansions:
            return False

        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators:
            return False

        atr_pct  = Decimal(str(indicators["atr_pct"]))
        atr_14   = Decimal(str(indicators["atr_14"]))

        # Wider spacing during a crash — use the high-volatility multiplier
        regime_cfg         = _REGIME_DEFAULTS["high"]
        expansion_mult     = Decimal(str(meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        expansion_spacing  = atr_pct * expansion_mult

        # Capital: fraction of original cycle capital split across new levels
        total_expansion_capital = Decimal(str(cycle["capital_allocated"])) * expansion_capital_frac
        capital_per_new_level   = total_expansion_capital / Decimal(str(expansion_levels_count))

        # Anchor: deepest existing level
        deepest_price  = min(Decimal(str(l["target_price"])) for l in levels)
        last_level_num = max(l["level_num"] for l in levels)

        maker_fee_pct = Decimal(str(strategy.get("maker_fee_pct", 0)))
        new_levels = []
        for i in range(1, expansion_levels_count + 1):
            new_target = deepest_price * (1 - i * expansion_spacing / 100)
            new_qty    = capital_per_new_level / (new_target * (1 + maker_fee_pct))
            new_levels.append({
                "level_num":        last_level_num + i,
                "target_price":     new_target,
                "weight":           Decimal("1"),
                "capital_allocated": capital_per_new_level,
                "quantity":         new_qty,
                "atr_value":        atr_14,
                "atr_multiplier":   expansion_mult,
                "level_trigger":    "crash_expansion",
            })

        new_stop_loss = new_levels[-1]["target_price"] * (1 - expansion_spacing / 100)

        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]

        async with conn.transaction():
            # 1. New grid levels
            await conn.executemany(
                """
                INSERT INTO base.trade_grid_levels
                    (cycle_id, strategy_id, level_num, target_price, weight,
                     capital_allocated, quantity, atr_value, atr_multiplier,
                     level_trigger, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'PENDING')
                """,
                [
                    (
                        cycle_id, strategy_id,
                        lvl["level_num"], float(lvl["target_price"]),
                        float(lvl["weight"]), float(lvl["capital_allocated"]),
                        float(lvl["quantity"]), float(lvl["atr_value"]),
                        float(lvl["atr_multiplier"]), lvl["level_trigger"],
                    )
                    for lvl in new_levels
                ],
            )

            # 2. New stop-loss
            await conn.execute(
                """
                UPDATE base.trade_cycles
                SET stop_loss_price = $1, updated_at = NOW()
                WHERE id = $2
                """,
                float(new_stop_loss), cycle_id,
            )

            # 3. System event
            await conn.execute(
                """
                INSERT INTO base.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'GRID_EXPANDED', 'INFO', $1, $2, $3, $4)
                """,
                strategy_id, cycle_id,
                f"Crash expansion: added {len(new_levels)} levels for cycle #{cycle['cycle_number']}",
                json.dumps({
                    "levels_added":              len(new_levels),
                    "deepest_anchor":            float(deepest_price),
                    "expansion_spacing_pct":     float(expansion_spacing),
                    "new_stop_loss":             float(new_stop_loss),
                    "expansion_capital_fraction": float(expansion_capital_frac),
                }),
            )

        logger.info(
            "Strategy %d | cycle %d: GRID EXPANDED — added %d crash levels below %.2f",
            strategy_id, cycle_id, len(new_levels), deepest_price,
        )

        return True

    # ── Auto-tuning ────────────────────────────────────────────────────────────

    async def _maybe_retune(
        self,
        conn,
        strategy: dict,
        cycle: dict,
        current_price: Decimal,
        levels: list,
    ) -> Decimal | None:
        """
        Detect a volatility-regime change and, if found, atomically:
          1. UPDATE trade_dca_cycle_details — new multiplier, spacing, profit target
          2. UPDATE trade_grid_levels       — re-price every PENDING level
          3. UPDATE trade_cycles            — new stop_loss_price
          4. INSERT system_events           — GRID_RETUNED

        Returns the new profit_target_pct (as a Decimal %) if a retune happened,
        None if the regime is unchanged or indicators are unavailable.

        High/extreme regime transitions are intentionally skipped here — those
        are handled by the crash-protection layer (not yet implemented).
        """
        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators:
            return None

        current_regime = indicators["volatility_regime"]
        stored_regime  = cycle["volatility_regime"]

        if current_regime == stored_regime:
            return None

        # High/extreme transitions are crash-protection territory, not auto-tune.
        if current_regime in ("high", "extreme"):
            logger.info(
                "Strategy %d | cycle %d: regime shifted to %s — skipping auto-tune "
                "(crash protection handles this).",
                strategy["id"], cycle["id"], current_regime,
            )
            return None

        meta       = strategy["metadata"] or {}
        regime_cfg = _REGIME_DEFAULTS.get(current_regime, _REGIME_DEFAULTS["normal"])

        new_multiplier    = Decimal(str(meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        new_profit_target = Decimal(str(meta.get(regime_cfg["profit_key"],     regime_cfg["profit"])))
        new_atr_14        = Decimal(str(indicators["atr_14"]))
        new_atr_pct       = Decimal(str(indicators["atr_pct"]))
        new_spacing       = new_atr_pct * new_multiplier   # remains a % value

        pending_levels = [l for l in levels if l["status"] == "PENDING"]
        open_levels    = [l for l in levels if l["status"] == "OPEN"]

        # Anchor re-spacing below the lowest OPEN level; fall back to current price.
        if open_levels:
            anchor_price = min(Decimal(str(l["target_price"])) for l in open_levels)
        else:
            anchor_price = current_price

        # Build update tuples for PENDING levels
        pending_updates: list[tuple] = []
        for i, level in enumerate(pending_levels, start=1):
            new_target  = anchor_price * (1 - i * new_spacing / 100)
            new_capital = Decimal(str(level["capital_allocated"]))
            new_qty     = new_capital / new_target
            pending_updates.append((
                float(new_target), float(new_qty),
                float(new_atr_14), float(new_multiplier),
                level["id"],
            ))

        # Stop-loss: one spacing below the deepest re-priced pending level.
        new_stop_loss: Decimal | None = None
        if pending_updates:
            deepest = anchor_price * (1 - len(pending_levels) * new_spacing / 100)
            new_stop_loss = deepest * (1 - new_spacing / 100)

        async with conn.transaction():
            # 1. trade_dca_cycle_details
            await conn.execute(
                """
                UPDATE base.trade_dca_cycle_details
                SET atr_multiplier    = $1,
                    grid_spacing_pct  = $2,
                    profit_target_pct = $3,
                    volatility_regime = $4,
                    last_tuned_at     = NOW()
                WHERE cycle_id = $5
                """,
                float(new_multiplier), float(new_spacing),
                float(new_profit_target), current_regime,
                cycle["id"],
            )

            # 2. Re-price PENDING grid levels
            if pending_updates:
                await conn.executemany(
                    """
                    UPDATE base.trade_grid_levels
                    SET target_price   = $1,
                        quantity       = $2,
                        atr_value      = $3,
                        atr_multiplier = $4,
                        level_trigger  = 'rebalance',
                        updated_at     = NOW()
                    WHERE id = $5
                    """,
                    pending_updates,
                )

            # 3. Update stop_loss_price on the cycle
            if new_stop_loss is not None:
                await conn.execute(
                    """
                    UPDATE base.trade_cycles
                    SET stop_loss_price = $1, updated_at = NOW()
                    WHERE id = $2
                    """,
                    float(new_stop_loss), cycle["id"],
                )

            # 4. system_events
            await conn.execute(
                """
                INSERT INTO base.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'GRID_RETUNED', 'INFO', $1, $2, $3, $4)
                """,
                strategy["id"], cycle["id"],
                f"Retuned DCA grid cycle #{cycle['cycle_number']}: {stored_regime} → {current_regime}",
                json.dumps({
                    "old_regime":              stored_regime,
                    "new_regime":              current_regime,
                    "new_multiplier":          float(new_multiplier),
                    "new_spacing_pct":         float(new_spacing),
                    "new_profit_target_pct":   float(new_profit_target),
                    "pending_levels_repriced": len(pending_updates),
                    "anchor_price":            float(anchor_price),
                }),
            )

        logger.info(
            "Strategy %d | cycle %d: RETUNED — regime %s→%s | "
            "spacing=%.2f%% profit_target=%.2f%% | %d pending levels repriced",
            strategy["id"], cycle["id"],
            stored_regime, current_regime,
            new_spacing, new_profit_target, len(pending_updates),
        )

        return new_profit_target

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _avg_entry(self, filled_levels: list) -> Decimal:
        """Weighted average entry price across all filled grid levels."""
        total_cost = sum(
            Decimal(str(l["target_price"])) * Decimal(str(l["quantity"]))
            for l in filled_levels
        )
        total_qty = sum(Decimal(str(l["quantity"])) for l in filled_levels)
        return total_cost / total_qty if total_qty > 0 else Decimal("0")
