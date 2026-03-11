"""
DCA Grid backtesting engine.

Pure simulation — no DB access.  Feed daily candles + indicator snapshots via
process_candle(), then call compute_metrics() to get results.

Fill assumptions (standard for daily OHLCV):
  - Limit buy filled if candle.low  <= target_price  (fill at target_price)
  - Take-profit sell filled at target_sell if candle.high >= target_sell
  - Stop-loss / circuit-breaker: fill at min(candle.open, trigger_price)
    to model overnight gap risk

Entry uses the previous candle's close as the reference price so a cycle
opened at end-of-day starts checking fills on the *next* candle.
"""

import logging
import math
from datetime import date
from decimal import Decimal

from trader_bot.strategies.dca_grid import DcaGridStrategy, _REGIME_DEFAULTS

from .models import BacktestCandle, BacktestCycle, BacktestLevel

logger = logging.getLogger(__name__)


class DcaGridBacktestEngine:

    def __init__(self, parameters: dict, initial_capital: float = 10_000.0):
        self.params          = parameters
        self.initial_capital = Decimal(str(initial_capital))
        self.free_capital    = Decimal(str(initial_capital))

        self.active_cycle:  BacktestCycle | None       = None
        self.closed_cycles: list[BacktestCycle]        = []
        self.equity_curve:  list[tuple[date, Decimal]] = []

        self._cycle_counter = 0
        self._strategy      = DcaGridStrategy()   # pure-method access only

    # ── Public API ──────────────────────────────────────────────────────────

    def process_candle(self, candle: BacktestCandle, indicators: dict | None) -> None:
        """Advance the simulation by one daily candle."""
        if indicators is None:
            self.equity_curve.append((candle.date, self._equity(candle.close)))
            return

        if self.active_cycle:
            self._process_active(candle, indicators)
        else:
            self._try_open(candle, indicators)

        self.equity_curve.append((candle.date, self._equity(candle.close)))

    def compute_metrics(self) -> dict:
        """
        Return a metrics dict keyed to the columns of base.backtest_runs.
        Close any open cycle at the last recorded close price before calling.
        """
        cycles = self.closed_cycles
        total  = len(cycles)

        # Use last equity curve entry so open-cycle mark-to-market is included.
        final_equity = (
            self.equity_curve[-1][1] if self.equity_curve else self.free_capital
        )
        total_return_pct = float(
            (final_equity - self.initial_capital) / self.initial_capital * 100
        )
        max_drawdown_pct = float(self._max_drawdown())

        pnls   = [float(c.net_pnl()) for c in cycles if c.net_pnl() is not None]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = (len(wins) / total * 100) if total > 0 else 0.0

        if losses and sum(losses) != 0:
            profit_factor = sum(wins) / abs(sum(losses))
        elif wins:
            profit_factor = None    # infinite — no losses
        else:
            profit_factor = 0.0

        sharpe_ratio = float(self._sharpe_ratio())

        avg_duration_days = (
            sum(
                (c.close_date - c.open_date).days
                for c in cycles if c.close_date
            ) / total
            if total > 0 else 0
        )

        return {
            "total_return_pct":        round(total_return_pct, 4),
            "max_drawdown_pct":        round(max_drawdown_pct, 4),
            "win_rate":                round(win_rate, 4),
            "profit_factor":           round(profit_factor, 6) if profit_factor is not None and math.isfinite(profit_factor) else profit_factor,
            "sharpe_ratio":            round(sharpe_ratio, 6),
            "total_cycles":            total,
            "avg_cycle_duration_secs": int(avg_duration_days * 86400),
        }

    # ── Cycle opening ────────────────────────────────────────────────────────

    def _try_open(self, candle: BacktestCandle, indicators: dict) -> None:
        meta = self.params

        passed, _ = self._strategy._check_entry_conditions(
            meta              = meta,
            volatility_regime = indicators["volatility_regime"],
            atr_pct           = Decimal(str(indicators["atr_pct"])),
            current_price     = candle.close,
            sma_50            = indicators.get("sma_50"),
            sma_200           = indicators.get("sma_200"),
            rsi_14            = indicators.get("rsi_14"),
        )
        if not passed:
            return

        capital_per_cycle = Decimal(str(meta.get("capital_per_cycle", 1000)))
        reserve_pct       = Decimal(str(meta.get("reserve_capital_pct", 30))) / 100
        deployable        = self.free_capital * (1 - reserve_pct)
        if deployable < capital_per_cycle:
            return

        self._open_cycle(candle, indicators, capital_per_cycle)

    def _open_cycle(
        self,
        candle: BacktestCandle,
        indicators: dict,
        capital_per_cycle: Decimal,
    ) -> None:
        meta = self.params
        regime    = indicators["volatility_regime"]
        atr_14    = Decimal(str(indicators["atr_14"]))
        atr_pct   = Decimal(str(indicators["atr_pct"]))

        regime_cfg    = _REGIME_DEFAULTS.get(regime, _REGIME_DEFAULTS["normal"])
        multiplier    = Decimal(str(meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        profit_target = Decimal(str(meta.get(regime_cfg["profit_key"],     regime_cfg["profit"])))
        spacing       = atr_pct * multiplier

        num_levels = int(meta.get("num_levels", 5))
        weights    = list(meta.get("weights", [1] * num_levels))
        if len(weights) != num_levels:
            weights = [1] * num_levels

        maker_fee_pct = Decimal(str(meta.get("maker_fee_pct", 0.0025)))
        raw_levels = self._strategy._compute_grid_levels(
            reference_price   = candle.close,
            grid_spacing_pct  = spacing,
            num_levels        = num_levels,
            weights           = weights,
            capital_per_cycle = capital_per_cycle,
            maker_fee_pct     = maker_fee_pct,
            atr_value         = atr_14,
            atr_multiplier    = multiplier,
        )
        levels = [
            BacktestLevel(
                level_num         = l["level_num"],
                target_price      = l["target_price"],
                quantity          = l["quantity"],
                capital_allocated = l["capital_allocated"],
                weight            = l["weight"],
                level_trigger     = l["level_trigger"],
            )
            for l in raw_levels
        ]

        deepest       = raw_levels[-1]["target_price"]
        stop_loss     = deepest * (1 - spacing / 100)
        taker_fee_pct = Decimal(str(meta.get("taker_fee_pct", 0.005)))

        self._cycle_counter += 1
        cycle = BacktestCycle(
            cycle_number      = self._cycle_counter,
            open_date         = candle.date,
            open_price        = candle.close,
            capital_allocated = capital_per_cycle,
            stop_loss_price   = stop_loss,
            profit_target_pct = profit_target,
            grid_spacing_pct  = spacing,
            atr_at_open       = atr_14,
            atr_multiplier    = multiplier,
            volatility_regime = regime,
            taker_fee_pct     = taker_fee_pct,
            levels            = levels,
        )

        self.active_cycle  = cycle
        self.free_capital -= capital_per_cycle

        logger.debug(
            "Backtest: opened cycle #%d on %s @ %.2f | "
            "spacing=%.2f%% profit_target=%.2f%% stop=%.2f",
            self._cycle_counter, candle.date, candle.close,
            spacing, profit_target, stop_loss,
        )

    # ── Active cycle processing ──────────────────────────────────────────────

    def _process_active(self, candle: BacktestCandle, indicators: dict) -> None:
        cycle = self.active_cycle
        meta  = self.params

        # 1. Circuit breaker
        cb_threshold = Decimal(str(meta.get("circuit_breaker_atr_pct", 8.0)))
        atr_pct      = Decimal(str(indicators["atr_pct"]))
        if indicators["volatility_regime"] == "extreme" or atr_pct >= cb_threshold:
            close_price = min(candle.open, cycle.stop_loss_price)
            self._close_cycle(candle, close_price, "circuit_breaker")
            return

        # 2. Stop-loss (model gap risk: fill at min of open and stop level)
        if candle.low <= cycle.stop_loss_price:
            close_price = min(candle.open, cycle.stop_loss_price)
            self._close_cycle(candle, close_price, "stop_loss")
            return

        # 3. Simulate limit-buy fills (filled at target_price when low touches it)
        for level in cycle.pending_levels:
            if candle.low <= level.target_price:
                level.status     = "FILLED"
                level.fill_price = level.target_price
                level.fill_date  = candle.date

        # 4. Auto-tune if regime changed to a non-crash regime
        current_regime = indicators["volatility_regime"]
        if current_regime != cycle.volatility_regime and current_regime not in ("high", "extreme"):
            self._retune_cycle(cycle, candle, indicators)

        # 5. Take-profit check
        filled = cycle.filled_levels
        if filled:
            avg_entry   = cycle.avg_entry()
            target_sell = avg_entry * (1 + cycle.profit_target_pct / 100 + cycle.taker_fee_pct)
            if candle.high >= target_sell:
                self._close_cycle(candle, target_sell, "take_profit")
                return

        # 6. Grid expansion when all pending levels have been consumed
        if not cycle.pending_levels and cycle.filled_levels:
            self._expand_cycle(cycle, candle, indicators)

    def _close_cycle(
        self,
        candle: BacktestCandle,
        close_price: Decimal,
        trigger: str,
    ) -> None:
        cycle = self.active_cycle
        if cycle is None:
            return

        # Cancel any remaining pending levels
        for level in cycle.levels:
            if level.status == "PENDING":
                level.status = "CANCELLED"

        cycle.status        = "CLOSED"
        cycle.close_date    = candle.date
        cycle.close_price   = close_price
        cycle.close_trigger = trigger

        # Return locked capital + realised P&L to free pool
        pnl = cycle.net_pnl() or Decimal("0")
        self.free_capital += cycle.capital_allocated + pnl

        self.closed_cycles.append(cycle)
        self.active_cycle = None

        logger.debug(
            "Backtest: closed cycle #%d on %s @ %.2f (%s) | pnl=%.2f",
            cycle.cycle_number, candle.date, close_price, trigger, pnl,
        )

    # ── Auto-tune ────────────────────────────────────────────────────────────

    def _retune_cycle(
        self,
        cycle: BacktestCycle,
        candle: BacktestCandle,
        indicators: dict,
    ) -> None:
        """Re-price pending levels and update profit target when regime changes."""
        new_regime = indicators["volatility_regime"]
        meta       = self.params
        regime_cfg = _REGIME_DEFAULTS.get(new_regime, _REGIME_DEFAULTS["normal"])

        new_multiplier    = Decimal(str(meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        new_profit_target = Decimal(str(meta.get(regime_cfg["profit_key"],     regime_cfg["profit"])))
        new_spacing       = Decimal(str(indicators["atr_pct"])) * new_multiplier

        pending = cycle.pending_levels
        filled  = cycle.filled_levels

        anchor = (
            min(l.fill_price or l.target_price for l in filled)
            if filled else candle.close
        )

        for i, level in enumerate(pending, start=1):
            new_target         = anchor * (1 - i * new_spacing / 100)
            level.target_price = new_target
            level.quantity     = level.capital_allocated / new_target
            level.level_trigger = "rebalance"

        if pending:
            deepest               = anchor * (1 - len(pending) * new_spacing / 100)
            cycle.stop_loss_price = deepest * (1 - new_spacing / 100)

        cycle.profit_target_pct = new_profit_target
        cycle.grid_spacing_pct  = new_spacing
        cycle.volatility_regime = new_regime

        logger.debug(
            "Backtest: retuned cycle #%d on %s: %s → %s | "
            "spacing=%.2f%% profit_target=%.2f%%",
            cycle.cycle_number, candle.date,
            cycle.volatility_regime, new_regime,
            new_spacing, new_profit_target,
        )

    # ── Grid expansion ───────────────────────────────────────────────────────

    def _expand_cycle(
        self,
        cycle: BacktestCycle,
        candle: BacktestCandle,
        indicators: dict,
    ) -> None:
        meta                   = self.params
        max_expansions         = int(meta.get("max_expansions", 1))
        expansion_levels_count = int(meta.get("expansion_levels", 2))
        expansion_capital_frac = Decimal(str(meta.get("expansion_capital_fraction", 0.3)))

        crash_count     = sum(1 for l in cycle.levels if l.level_trigger == "crash_expansion")
        expansions_done = crash_count // expansion_levels_count if expansion_levels_count > 0 else 0
        if expansions_done >= max_expansions:
            return

        atr_pct    = Decimal(str(indicators["atr_pct"]))
        regime_cfg = _REGIME_DEFAULTS["high"]
        exp_mult   = Decimal(str(meta.get(regime_cfg["multiplier_key"], regime_cfg["multiplier"])))
        exp_spacing = atr_pct * exp_mult

        # Only expand if we have enough free capital
        total_exp_capital = cycle.capital_allocated * expansion_capital_frac
        if self.free_capital < total_exp_capital:
            return

        cap_per_level  = total_exp_capital / Decimal(str(expansion_levels_count))
        deepest_price  = min(l.target_price for l in cycle.levels)
        last_level_num = max(l.level_num for l in cycle.levels)

        for i in range(1, expansion_levels_count + 1):
            new_target = deepest_price * (1 - i * exp_spacing / 100)
            cycle.levels.append(BacktestLevel(
                level_num         = last_level_num + i,
                target_price      = new_target,
                quantity          = cap_per_level / new_target,
                capital_allocated = cap_per_level,
                level_trigger     = "crash_expansion",
            ))

        new_deepest           = deepest_price * (1 - expansion_levels_count * exp_spacing / 100)
        cycle.stop_loss_price = new_deepest * (1 - exp_spacing / 100)

        self.free_capital       -= total_exp_capital
        cycle.capital_allocated += total_exp_capital

        logger.debug(
            "Backtest: expanded cycle #%d on %s — added %d crash levels below %.2f",
            cycle.cycle_number, candle.date, expansion_levels_count, deepest_price,
        )

    # ── Equity & metrics helpers ─────────────────────────────────────────────

    def _equity(self, current_price: Decimal) -> Decimal:
        """Total equity = free capital + mark-to-market value of open cycle."""
        equity = self.free_capital
        if self.active_cycle:
            cycle = self.active_cycle
            qty   = cycle.total_qty()
            if qty > 0:
                unrealised = current_price * qty - cycle.total_cost()
                equity    += cycle.capital_allocated + unrealised
            else:
                # No fills yet — all capital locked, mark at cost
                equity += cycle.capital_allocated
        return equity

    def _max_drawdown(self) -> Decimal:
        if not self.equity_curve:
            return Decimal("0")
        peak   = self.equity_curve[0][1]
        max_dd = Decimal("0")
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else Decimal("0")
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _sharpe_ratio(self) -> float:
        """Annualised Sharpe using daily equity returns (risk-free rate = 0)."""
        if len(self.equity_curve) < 2:
            return 0.0
        returns = []
        for i in range(1, len(self.equity_curve)):
            prev = self.equity_curve[i - 1][1]
            curr = self.equity_curve[i][1]
            if prev > 0:
                returns.append(float((curr - prev) / prev))
        if not returns:
            return 0.0
        mean     = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std      = math.sqrt(variance)
        return (mean / std * math.sqrt(252)) if std > 0 else 0.0
