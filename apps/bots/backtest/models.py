"""
Immutable data models for the DCA grid backtester.

BacktestCandle   — one daily OHLCV bar
BacktestLevel    — a single grid level (mutable status during simulation)
BacktestCycle    — an entire grid cycle from open to close
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class BacktestCandle:
    date:   date
    open:   Decimal
    high:   Decimal
    low:    Decimal
    close:  Decimal
    volume: Decimal


@dataclass
class BacktestLevel:
    level_num:         int
    target_price:      Decimal
    quantity:          Decimal
    capital_allocated: Decimal
    weight:            Decimal = Decimal("1")
    level_trigger:     str     = "initial"     # initial | rebalance | crash_expansion
    status:            str     = "PENDING"     # PENDING | FILLED | CANCELLED
    fill_price:        Decimal | None = None
    fill_date:         date    | None = None


@dataclass
class BacktestCycle:
    cycle_number:      int
    open_date:         date
    open_price:        Decimal
    capital_allocated: Decimal
    stop_loss_price:   Decimal
    profit_target_pct: Decimal   # e.g. Decimal("1.5") means 1.5%
    grid_spacing_pct:  Decimal
    atr_at_open:       Decimal
    atr_multiplier:    Decimal
    volatility_regime: str
    taker_fee_pct:     Decimal
    levels:            list[BacktestLevel] = field(default_factory=list)

    # Lifecycle
    status:        str          = "OPEN"
    close_date:    date | None  = None
    close_price:   Decimal | None = None
    close_trigger: str  | None  = None   # take_profit | stop_loss | circuit_breaker | end_of_backtest

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def filled_levels(self) -> list[BacktestLevel]:
        return [l for l in self.levels if l.status == "FILLED"]

    @property
    def pending_levels(self) -> list[BacktestLevel]:
        return [l for l in self.levels if l.status == "PENDING"]

    def total_qty(self) -> Decimal:
        return sum(l.quantity for l in self.filled_levels)

    def total_cost(self) -> Decimal:
        return sum(
            (l.fill_price or l.target_price) * l.quantity
            for l in self.filled_levels
        )

    def avg_entry(self) -> Decimal:
        qty = self.total_qty()
        return self.total_cost() / qty if qty > 0 else Decimal("0")

    def undeployed_capital(self) -> Decimal:
        """Capital locked in this cycle but not yet spent on fills."""
        return self.capital_allocated - self.total_cost()

    def net_pnl(self) -> Decimal | None:
        """
        Net P&L for a closed cycle.
        gross = sell_proceeds - buy_cost
        fees  = (buy_cost + sell_proceeds) * taker_fee_pct
        net   = gross - fees
        """
        if self.status != "CLOSED" or self.close_price is None:
            return None
        qty      = self.total_qty()
        cost     = self.total_cost()
        proceeds = self.close_price * qty
        fees     = (cost + proceeds) * self.taker_fee_pct
        return proceeds - cost - fees
