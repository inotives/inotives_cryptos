"""
Trend Following strategy implementation.

Enters a long position when the market is in a confirmed uptrend and exits
via a rising trailing stop. Designed to complement the DCA Grid strategy in
the Hybrid Grid + Regime-Switching system.

Entry conditions (ALL must pass):
  1. Regime Score >= 61 from inotives_tradings.asset_market_regime (strong trend)
  2. EMA50 > EMA200 (golden cross — sustained uptrend)
  3. Current price > 5-day high (breakout confirmation)
  4. ADX(14) >= min_adx (trend is strong, default 25)
  5. RSI(14) < rsi_entry_max (not overbought, default 70)
  6. ATR% < max_atr_pct_entry (not in extreme volatility, default 6)

Exit logic:
  - Initial stop loss : entry_price - (atr_stop_multiplier * ATR)
  - Trailing stop     : highest_price_since_entry - (atr_trail_multiplier * ATR)
  - Effective stop    : MAX(initial_stop, trailing_stop) — stop only moves UP
  - Trigger           : current_price <= effective_stop

Re-entry:
  After a stop-out, the bot waits for:
    price > EMA50  AND  price > new 5-day high
  before opening a new cycle.

Position sizing (ATR-scaled risk):
  capital_risk = capital_allocated * risk_pct_per_trade
  position_size = capital_risk / (ATR * atr_stop_multiplier)
  Capped at: capital_allocated / current_price (never spend more than allocated).

Expected strategy metadata (inotives_tradings.trade_strategies.metadata):
{
    "capital_allocated":    1000,   -- Quote currency to risk on this strategy
    "risk_pct_per_trade":   1.0,    -- % of capital_allocated to risk per trade
    "atr_stop_multiplier":  2.0,    -- Initial SL = entry - N * ATR
    "atr_trail_multiplier": 3.0,    -- Trailing SL = highest - N * ATR
    "min_adx":              25.0,   -- Minimum ADX(14) for entry
    "min_regime_score":     61.0,   -- Minimum regime score for entry
    "rsi_entry_max":        70.0,   -- Skip entry if RSI >= this
    "max_atr_pct_entry":    6.0,    -- Skip entry if ATR% >= this
    "reserve_capital_pct":  20      -- % of balance kept in reserve
}

Cycle state in trade_cycles.metadata:
{
    "entry_price":               48000.0,
    "position_size":             0.0208,
    "atr_at_entry":              1200.0,
    "initial_stop_loss":         45600.0,   -- entry - 2*ATR (never changes)
    "highest_price_since_entry": 52000.0,   -- updated every tick
    "entry_order_id":            "abc123"   -- exchange order id
}
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from common.connections.base import BaseExchangeConnection
from common.db import get_conn

from .base import BaseStrategy
from bots.trader_bot.hybrid_coordinator import (
    get_regime_score_with_circuit_breaker,
    grid_has_active_cycle,
    trend_capital_limit,
    REGIME_PRIORITY_PIVOT,
)

logger = logging.getLogger(__name__)


class TrendFollowingStrategy(BaseStrategy):

    strategy_type = "TREND_FOLLOW"

    # ── Main tick ───────────────────────────────────────────────────────────────

    async def process(self, exchange: BaseExchangeConnection, strategy: dict, paper: bool = False) -> None:
        self._paper = paper
        strategy_id = strategy["id"]

        async with get_conn() as conn:
            # Check for an existing OPEN cycle
            cycle = await conn.fetchrow(
                """
                SELECT * FROM inotives_tradings.trade_cycles
                WHERE strategy_id = $1 AND status = 'OPEN'
                ORDER BY cycle_number DESC LIMIT 1
                """,
                strategy_id,
            )

            if not cycle:
                await self._maybe_open_cycle(exchange, conn, strategy)
                return

            # Manage the open position
            await self._manage_open_position(exchange, conn, strategy, cycle)

    # ── Cycle opening ───────────────────────────────────────────────────────────

    async def _maybe_open_cycle(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
    ) -> None:
        """Check all entry conditions and open a cycle if they pass."""
        strategy_id = strategy["id"]
        meta = strategy["metadata"] or {}

        # ── 1. Load indicators + regime ────────────────────────────────────────
        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators:
            logger.info("Strategy %d: no indicator data — skipping entry check.", strategy_id)
            return

        # Use coordinator to get regime score with intraday circuit breaker.
        # Returns 0.0 if circuit breaker fires, None if no regime data yet.
        regime_score_raw = await get_regime_score_with_circuit_breaker(
            conn,
            asset_id       = strategy["base_asset_id"],
            base_asset_id  = strategy["base_asset_id"],
            quote_asset_id = strategy["quote_asset_id"],
        )
        if regime_score_raw is None:
            logger.info("Strategy %d: no regime data — skipping entry check.", strategy_id)
            return

        # Build a regime dict for _check_entry_conditions (expects final_regime_score key)
        regime = {"final_regime_score": regime_score_raw}

        # ── 2. Load current price ──────────────────────────────────────────────
        price_row = await conn.fetchrow(
            """
            SELECT observed_price FROM inotives_tradings.price_observations
            WHERE base_asset_id = $1 AND quote_asset_id = $2
            ORDER BY observed_at DESC LIMIT 1
            """,
            strategy["base_asset_id"],
            strategy["quote_asset_id"],
        )
        if not price_row:
            logger.info("Strategy %d: no price data — skipping entry check.", strategy_id)
            return

        current_price = Decimal(str(price_row["observed_price"]))

        # ── 3. Load 5-day high ─────────────────────────────────────────────────
        high_5d = await self._load_5d_high(conn, strategy["base_asset_id"])
        if high_5d is None:
            logger.info("Strategy %d: no 5-day high available — skipping.", strategy_id)
            return

        # ── 4. Entry conditions ────────────────────────────────────────────────
        passed, reason = self._check_entry_conditions(
            meta, indicators, regime, current_price, high_5d,
        )
        if not passed:
            logger.info("Strategy %d: entry conditions not met — %s", strategy_id, reason)
            return

        # ── 4b. Intraday RSI guard ───────────────────────────────────────────
        # Daily RSI may be below threshold while intraday RSI is overbought.
        # Check live 1h RSI to avoid entering at intraday peaks.
        symbol = (
            f"{strategy['base_asset_code'].upper()}"
            f"/{strategy['quote_asset_code'].upper()}"
        )
        rsi_entry_max = float(meta.get("rsi_entry_max", 70.0))
        intraday_rsi = await self._load_intraday_rsi(exchange, symbol)
        if intraday_rsi is not None and intraday_rsi >= rsi_entry_max:
            logger.info(
                "Strategy %d: intraday RSI(1h)=%.1f >= %.1f — skipping entry (overbought intraday).",
                strategy_id, intraday_rsi, rsi_entry_max,
            )
            return

        # ── 5. Capital check ───────────────────────────────────────────────────
        capital_allocated = Decimal(str(meta.get("capital_allocated", 1000)))
        reserve_pct = Decimal(str(meta.get("reserve_capital_pct", 20))) / 100

        # Hybrid priority: RS 31–60 (transition zone) — if a grid cycle is active,
        # trend only enters if it has its own capital allocation (RS > 50 means
        # trend already has priority, so no conflict).  When RS <= 50, grid has
        # priority; trend only enters if no active grid cycle is using capital.
        if regime_score_raw <= REGIME_PRIORITY_PIVOT:
            if await grid_has_active_cycle(conn, strategy["base_asset_id"]):
                logger.info(
                    "Strategy %d: RS=%.1f <= 50 and DCA_GRID cycle is open "
                    "— grid has capital priority, deferring trend entry.",
                    strategy_id, regime_score_raw,
                )
                return

        # Regime-scale: trend capital grows proportionally with RS.
        # At RS=61 the trend gets 61% of its configured allocation;
        # at RS=100 it gets 100%.
        capital_allocated = trend_capital_limit(capital_allocated, regime_score_raw)
        logger.debug(
            "Strategy %d: regime-scaled trend capital: %.2f (RS=%.1f)",
            strategy_id, capital_allocated, regime_score_raw,
        )

        available = await self._available_capital(exchange, conn, strategy)
        if available is None:
            logger.warning("Strategy %d: could not determine available capital.", strategy_id)
            return

        deployable = available * (1 - reserve_pct)
        if deployable < capital_allocated:
            logger.info(
                "Strategy %d: insufficient capital — deployable=%.2f required=%.2f",
                strategy_id, deployable, capital_allocated,
            )
            return

        # ── 6. Compute position size ────────────────────────────────────────────
        atr_14 = Decimal(str(indicators["atr_14"]))
        atr_stop_multiplier  = Decimal(str(meta.get("atr_stop_multiplier", 2.0)))
        risk_pct_per_trade   = Decimal(str(meta.get("risk_pct_per_trade", 1.0))) / 100

        capital_at_risk = capital_allocated * risk_pct_per_trade
        position_size   = capital_at_risk / (atr_14 * atr_stop_multiplier)

        # Cap: never commit more quote than capital_allocated
        max_position = capital_allocated / current_price
        position_size = min(position_size, max_position)

        # Adjust for taker fee
        taker_fee = Decimal(str(strategy.get("taker_fee_pct", 0.005)))
        position_size = position_size / (1 + taker_fee)

        initial_stop_loss = current_price - (atr_stop_multiplier * atr_14)

        # ── 7. Place market buy ────────────────────────────────────────────────
        if self._paper:
            # Paper mode: assume fill at current price, no exchange order
            exchange_order_id = ""
            fill_price = current_price
        else:
            order_result = await exchange.create_order(
                symbol=symbol,
                order_type="market",
                side="buy",
                amount=float(position_size),
            )
            exchange_order_id = order_result.get("id", "") if order_result else ""
            fill_price = Decimal(str(order_result.get("price") or current_price)) if order_result else current_price

        logger.info(
            "Strategy %d: %sTREND_FOLLOW entry — price=%.4f size=%.6f stop=%.4f | %s",
            strategy_id, "[PAPER] " if self._paper else "",
            fill_price, position_size, initial_stop_loss, reason,
        )

        # ── 8. Persist cycle ───────────────────────────────────────────────────
        await self._open_cycle(
            conn=conn,
            strategy=strategy,
            entry_price=fill_price,
            position_size=position_size,
            capital_allocated=capital_allocated,
            initial_stop_loss=initial_stop_loss,
            atr_at_entry=atr_14,
            high_5d_at_entry=Decimal(str(high_5d)),
            exchange_order_id=exchange_order_id,
        )

    def _check_entry_conditions(
        self,
        meta: dict,
        indicators: dict,
        regime: dict,
        current_price: Decimal,
        high_5d: float,
    ) -> tuple[bool, str]:
        """
        Validate all entry conditions.
        Returns (passed: bool, reason: str).
        """
        min_regime_score = float(meta.get("min_regime_score", 61.0))
        min_adx          = float(meta.get("min_adx", 25.0))
        rsi_entry_max    = float(meta.get("rsi_entry_max", 70.0))
        max_atr_pct      = float(meta.get("max_atr_pct_entry", 6.0))

        regime_score = float(regime["final_regime_score"])
        if regime_score < min_regime_score:
            return False, f"regime_score={regime_score:.1f} < {min_regime_score} (not trending)"

        ema_50  = indicators.get("ema_50")
        ema_200 = indicators.get("ema_200")
        if ema_50 is None or ema_200 is None:
            return False, "EMA50/200 not available"
        if float(ema_50) <= float(ema_200):
            return False, f"no golden cross: ema_50={float(ema_50):.2f} <= ema_200={float(ema_200):.2f}"

        if float(current_price) <= high_5d:
            return False, f"price={float(current_price):.4f} not above 5d-high={high_5d:.4f}"

        adx_14 = indicators.get("adx_14")
        if adx_14 is None or float(adx_14) < min_adx:
            adx_str = f"{float(adx_14):.1f}" if adx_14 is not None else "n/a"
            return False, f"adx_14={adx_str} < {min_adx} (weak trend)"

        rsi_14 = indicators.get("rsi_14")
        if rsi_14 is not None and float(rsi_14) >= rsi_entry_max:
            return False, f"rsi_14={float(rsi_14):.1f} >= {rsi_entry_max} (overbought)"

        atr_pct = indicators.get("atr_pct")
        if atr_pct is not None and float(atr_pct) >= max_atr_pct:
            return False, f"atr_pct={float(atr_pct):.2f}% >= {max_atr_pct}% (extreme volatility)"

        return True, (
            f"regime={regime_score:.1f} adx={float(adx_14):.1f} "
            f"price={float(current_price):.2f} > 5d_high={high_5d:.2f}"
        )

    async def _open_cycle(
        self,
        conn,
        strategy: dict,
        entry_price: Decimal,
        position_size: Decimal,
        capital_allocated: Decimal,
        initial_stop_loss: Decimal,
        atr_at_entry: Decimal,
        high_5d_at_entry: Decimal,
        exchange_order_id: str,
    ) -> None:
        """Write a new cycle and all associated records in a single transaction."""
        strategy_id = strategy["id"]

        cycle_meta = {
            "entry_price":               float(entry_price),
            "position_size":             float(position_size),
            "atr_at_entry":              float(atr_at_entry),
            "initial_stop_loss":         float(initial_stop_loss),
            "highest_price_since_entry": float(entry_price),
            "high_5d_at_entry":          float(high_5d_at_entry),
            "entry_order_id":            exchange_order_id,
        }

        async with conn.transaction():
            cycle_number = await conn.fetchval(
                """
                SELECT COALESCE(MAX(cycle_number), 0) + 1
                FROM inotives_tradings.trade_cycles
                WHERE strategy_id = $1
                """,
                strategy_id,
            )

            cycle_id = await conn.fetchval(
                """
                INSERT INTO inotives_tradings.trade_cycles
                    (strategy_id, cycle_number, capital_allocated, status,
                     stop_loss_price, opened_at, metadata)
                VALUES ($1, $2, $3, 'OPEN', $4, NOW(), $5::jsonb)
                RETURNING id
                """,
                strategy_id, cycle_number,
                float(capital_allocated), float(initial_stop_loss),
                json.dumps(cycle_meta),
            )

            # Record the entry buy order (skip in paper mode)
            if not self._paper:
                await conn.execute(
                    """
                    INSERT INTO inotives_tradings.trade_orders
                        (cycle_id, strategy_id, exchange_order_id, side, order_type,
                         target_price, quantity, status, submitted_at,
                         metadata)
                    VALUES ($1, $2, $3, 'BUY', 'MARKET', $4, $5, 'FILLED', NOW(),
                            $6::jsonb)
                    """,
                    cycle_id, strategy_id, exchange_order_id or None,
                    float(entry_price), float(position_size),
                    json.dumps({"trigger": "trend_entry", "stop_loss": float(initial_stop_loss)}),
                )

            # Capital lock
            await conn.execute(
                """
                INSERT INTO inotives_tradings.capital_locks
                    (venue_id, asset_id, cycle_id, strategy_id, amount, status, locked_at)
                VALUES ($1, $2, $3, $4, $5, 'ACTIVE', NOW())
                """,
                strategy["venue_id"], strategy["quote_asset_id"],
                cycle_id, strategy_id, float(capital_allocated),
            )

            await conn.execute(
                """
                INSERT INTO inotives_tradings.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'CYCLE_OPENED', 'INFO', $1, $2, $3, $4::jsonb)
                """,
                strategy_id, cycle_id,
                f"{'[PAPER] ' if self._paper else ''}Opened TREND_FOLLOW cycle #{cycle_number} for strategy {strategy_id}",
                json.dumps({
                    "paper_mode":      self._paper,
                    "cycle_number":    cycle_number,
                    "entry_price":     float(entry_price),
                    "position_size":   float(position_size),
                    "stop_loss_price": float(initial_stop_loss),
                    "atr_at_entry":    float(atr_at_entry),
                    "capital":         float(capital_allocated),
                }),
            )

        logger.info(
            "Strategy %d: opened TREND_FOLLOW cycle #%d | entry=%.4f | "
            "size=%.6f | stop=%.4f | capital=%.2f",
            strategy_id, cycle_number, entry_price,
            position_size, initial_stop_loss, capital_allocated,
        )

    # ── Open position management ────────────────────────────────────────────────

    async def _manage_open_position(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle,
    ) -> None:
        """
        On each tick:
          1. Fetch current price.
          2. Update highest_price_since_entry in metadata.
          3. Compute trailing stop = highest - (atr_trail_multiplier * ATR).
          4. Effective stop = MAX(initial_stop, trailing_stop).
          5. If price <= effective_stop → close position.
        """
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]
        meta = strategy["metadata"] or {}

        cycle_meta = cycle["metadata"]
        if isinstance(cycle_meta, str):
            cycle_meta = json.loads(cycle_meta)

        # Load current price
        price_row = await conn.fetchrow(
            """
            SELECT observed_price FROM inotives_tradings.price_observations
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

        # Load ATR for trailing stop — prefer live intraday ATR, fall back to daily
        symbol = (
            f"{strategy['base_asset_code'].upper()}"
            f"/{strategy['quote_asset_code'].upper()}"
        )
        intraday_atr = await self._load_intraday_atr(exchange, symbol)

        indicators = await self._load_latest_indicators(conn, strategy["base_asset_id"])
        if not indicators and intraday_atr is None:
            logger.warning("Strategy %d: no indicator data on open cycle tick.", strategy_id)
            return

        daily_atr = Decimal(str(indicators["atr_14"])) if indicators else None
        atr_14 = intraday_atr if intraday_atr is not None else daily_atr
        if atr_14 is None:
            logger.warning("Strategy %d: no ATR available, skipping tick.", strategy_id)
            return

        if intraday_atr is not None and daily_atr is not None:
            logger.debug(
                "Strategy %d: trailing stop ATR — intraday=%.4f daily=%.4f (using intraday)",
                strategy_id, intraday_atr, daily_atr,
            )
        atr_trail_multiplier = Decimal(str(meta.get("atr_trail_multiplier", 3.0)))
        initial_stop         = Decimal(str(cycle_meta["initial_stop_loss"]))
        highest              = Decimal(str(cycle_meta["highest_price_since_entry"]))
        position_size        = Decimal(str(cycle_meta["position_size"]))
        entry_price          = Decimal(str(cycle_meta["entry_price"]))

        # Update highest price seen since entry
        if current_price > highest:
            highest = current_price
            cycle_meta["highest_price_since_entry"] = float(highest)
            await conn.execute(
                """
                UPDATE inotives_tradings.trade_cycles
                SET metadata = $1::jsonb, updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(cycle_meta), cycle_id,
            )

        # Trailing stop: highest - N * ATR
        trailing_stop = highest - (atr_trail_multiplier * atr_14)

        # Effective stop only moves up — never lower than the initial hard stop
        effective_stop = max(initial_stop, trailing_stop)

        logger.info(
            "Strategy %d | cycle %d | price=%.4f | entry=%.4f | "
            "highest=%.4f | trail_stop=%.4f | init_stop=%.4f | eff_stop=%.4f",
            strategy_id, cycle_id, current_price, entry_price,
            highest, trailing_stop, initial_stop, effective_stop,
        )

        # Update stop_loss_price on the cycle row so dashboards reflect current level
        await conn.execute(
            """
            UPDATE inotives_tradings.trade_cycles
            SET stop_loss_price = $1, updated_at = NOW()
            WHERE id = $2
            """,
            float(effective_stop), cycle_id,
        )

        # Exit check
        if current_price <= effective_stop:
            trigger = (
                "trailing_stop" if trailing_stop > initial_stop else "initial_stop"
            )
            logger.warning(
                "Strategy %d: %s triggered — price=%.4f stop=%.4f",
                strategy_id, trigger, current_price, effective_stop,
            )
            await self._close_position(
                exchange, conn, strategy, cycle, cycle_meta,
                current_price, position_size, effective_stop, trigger,
            )

    async def _close_position(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
        cycle,
        cycle_meta: dict,
        current_price: Decimal,
        position_size: Decimal,
        effective_stop: Decimal,
        trigger: str,
    ) -> None:
        """Place a market sell and close the cycle."""
        strategy_id = strategy["id"]
        cycle_id    = cycle["id"]

        entry_price = Decimal(str(cycle_meta["entry_price"]))

        if self._paper:
            # Paper mode: use current price as simulated sell price
            fill_price = current_price
        else:
            symbol = (
                f"{strategy['base_asset_code'].upper()}"
                f"/{strategy['quote_asset_code'].upper()}"
            )
            order_result = await exchange.create_order(
                symbol=symbol,
                order_type="market",
                side="sell",
                amount=float(position_size),
            )
            exchange_order_id = order_result.get("id", "") if order_result else ""
            fill_price = Decimal(str(order_result.get("price") or current_price)) if order_result else current_price

        pnl_pct = float((fill_price - entry_price) / entry_price * 100)

        async with conn.transaction():
            # Record sell order (skip in paper mode)
            if not self._paper:
                await conn.execute(
                    """
                    INSERT INTO inotives_tradings.trade_orders
                        (cycle_id, strategy_id, exchange_order_id, side, order_type,
                         target_price, quantity, status, submitted_at, metadata)
                    VALUES ($1, $2, $3, 'SELL', 'MARKET', $4, $5, 'FILLED', NOW(), $6::jsonb)
                    """,
                    cycle_id, strategy_id, exchange_order_id or None,
                    float(fill_price), float(position_size),
                    json.dumps({"trigger": trigger, "entry_price": float(entry_price)}),
                )

            await conn.execute(
                """
                UPDATE inotives_tradings.trade_cycles
                SET status        = 'CLOSED',
                    close_trigger = $1,
                    closed_at     = NOW(),
                    updated_at    = NOW()
                WHERE id = $2
                """,
                trigger, cycle_id,
            )

            # Release capital lock
            await conn.execute(
                """
                UPDATE inotives_tradings.capital_locks
                SET status     = 'RELEASED',
                    updated_at = NOW()
                WHERE cycle_id = $1 AND status = 'ACTIVE'
                """,
                cycle_id,
            )

            await conn.execute(
                """
                INSERT INTO inotives_tradings.system_events
                    (bot_name, event_type, severity, strategy_id, cycle_id, message, payload)
                VALUES ('trader_bot', 'CYCLE_CLOSED', 'INFO', $1, $2, $3, $4::jsonb)
                """,
                strategy_id, cycle_id,
                f"{'[PAPER] ' if self._paper else ''}Closed TREND_FOLLOW cycle via {trigger} for strategy {strategy_id}",
                json.dumps({
                    "paper_mode":     self._paper,
                    "trigger":        trigger,
                    "entry_price":    float(entry_price),
                    "exit_price":     float(fill_price),
                    "pnl_pct":        round(pnl_pct, 4),
                    "effective_stop": float(effective_stop),
                }),
            )

        logger.info(
            "Strategy %d: %sTREND_FOLLOW cycle %d CLOSED via %s | "
            "entry=%.4f exit=%.4f pnl=%.2f%%",
            strategy_id, "[PAPER] " if self._paper else "",
            cycle_id, trigger, entry_price, fill_price, pnl_pct,
        )

    # ── Intraday helpers ────────────────────────────────────────────────────────

    async def _load_intraday_atr(
        self,
        exchange: BaseExchangeConnection,
        symbol: str,
        period: int = 14,
        timeframe: str = "1h",
    ) -> Decimal | None:
        """
        Compute ATR from live exchange candles.

        Returns None if data is unavailable or insufficient.
        Uses (period * 2 + 1) candles so the smoothing is well-seeded.
        """
        try:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=period * 2 + 1,
            )
            if not candles or len(candles) < period + 1:
                logger.debug(
                    "Intraday ATR: insufficient candles (%d) for period %d",
                    len(candles) if candles else 0, period,
                )
                return None
            atr = self._compute_atr(candles, period)
            if atr is not None:
                logger.debug(
                    "Intraday ATR(%d, %s): %.6f  [%d candles]",
                    period, timeframe, atr, len(candles),
                )
            return Decimal(str(atr)) if atr is not None else None
        except Exception as exc:
            logger.warning("Could not fetch intraday OHLCV for ATR (%s): %s", symbol, exc)
            return None

    async def _load_intraday_rsi(
        self,
        exchange: BaseExchangeConnection,
        symbol: str,
        period: int = 14,
        timeframe: str = "1h",
    ) -> float | None:
        """
        Compute RSI from live exchange candles.

        Returns None if data is unavailable or insufficient.
        """
        try:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=period * 2 + 1,
            )
            if not candles or len(candles) < period + 1:
                return None
            closes = [c["close"] for c in candles]
            return self._compute_rsi(closes, period)
        except Exception as exc:
            logger.warning("Could not fetch intraday OHLCV for RSI (%s): %s", symbol, exc)
            return None

    @staticmethod
    def _compute_atr(candles: list[dict], period: int = 14) -> float | None:
        """
        ATR using Wilder's smoothing from OHLCV candle dicts.

        Each candle must have 'high', 'low', 'close' keys.
        Requires at least period+1 candles.
        """
        if len(candles) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        # Seed: simple average of first `period` TRs
        atr = sum(true_ranges[:period]) / period

        # Wilder smoothing
        for i in range(period, len(true_ranges)):
            atr = (atr * (period - 1) + true_ranges[i]) / period

        return round(atr, 8)

    @staticmethod
    def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
        """RSI using Wilder's smoothing."""
        if len(closes) < period + 1:
            return None

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [abs(min(d, 0.0)) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1 + rs)), 2)

    # ── Data loading helpers ────────────────────────────────────────────────────

    async def _load_latest_indicators(self, conn, asset_id: int) -> dict | None:
        """Load the most recent asset_indicators_1d row with all required fields."""
        row = await conn.fetchrow(
            """
            SELECT atr_14, atr_pct, ema_50, ema_200, adx_14,
                   ema_slope_5d, vol_ratio_14, rsi_14, metric_date
            FROM inotives_tradings.asset_indicators_1d
            WHERE asset_id = $1
              AND atr_14   IS NOT NULL
              AND ema_50   IS NOT NULL
              AND ema_200  IS NOT NULL
              AND adx_14   IS NOT NULL
            ORDER BY metric_date DESC
            LIMIT 1
            """,
            asset_id,
        )
        return dict(row) if row else None

    async def _load_latest_regime(self, conn, asset_id: int) -> dict | None:
        """Load the most recent asset_market_regime row."""
        row = await conn.fetchrow(
            """
            SELECT final_regime_score, score_adx, score_slope, score_vol,
                   raw_adx, raw_slope, raw_vol_ratio, metric_date
            FROM inotives_tradings.asset_market_regime
            WHERE asset_id = $1
              AND final_regime_score IS NOT NULL
            ORDER BY metric_date DESC
            LIMIT 1
            """,
            asset_id,
        )
        return dict(row) if row else None

    async def _load_5d_high(self, conn, asset_id: int) -> float | None:
        """
        Return the highest close price over the previous 5 completed days
        (excluding today). Used as the breakout trigger level.
        """
        row = await conn.fetchrow(
            """
            SELECT MAX(close_price) AS high_5d
            FROM (
                SELECT close_price
                FROM inotives_tradings.asset_metrics_1d
                WHERE asset_id = $1
                  AND is_final  = true
                ORDER BY metric_date DESC
                LIMIT 5
            ) sub
            """,
            asset_id,
        )
        if row and row["high_5d"] is not None:
            return float(row["high_5d"])
        return None

    async def _available_capital(
        self,
        exchange: BaseExchangeConnection,
        conn,
        strategy: dict,
    ) -> Decimal | None:
        """Return available quote-asset capital for this strategy's venue."""
        row = await conn.fetchrow(
            """
            SELECT available_balance
            FROM inotives_tradings.venue_available_capital
            WHERE venue_id = $1 AND asset_id = $2
            """,
            strategy["venue_id"], strategy["quote_asset_id"],
        )
        if row is not None:
            return Decimal(str(row["available_balance"]))

        try:
            balance    = await exchange.fetch_balance()
            quote_code = strategy["quote_asset_code"].upper()
            free       = balance.get(quote_code, {}).get("free", 0)
            return Decimal(str(free))
        except Exception as exc:
            logger.warning("Strategy %d: fetch_balance failed: %s", strategy["id"], exc)
            return None
