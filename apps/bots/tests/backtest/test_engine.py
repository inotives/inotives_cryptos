"""
Unit tests for DcaGridBacktestEngine.

All tests are purely synchronous — the engine has no I/O.
Candles and indicators are constructed inline; no DB required.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backtest.engine import DcaGridBacktestEngine
from backtest.models import BacktestCandle, BacktestCycle, BacktestLevel


# ---------------------------------------------------------------------------
# Shared fixtures & helpers
# ---------------------------------------------------------------------------

BASE_PARAMS = {
    "capital_per_cycle":          1000,
    "num_levels":                 3,
    "weights":                    [1, 2, 3],
    "atr_multiplier_low":         0.4,
    "atr_multiplier_normal":      0.5,
    "atr_multiplier_high":        0.7,
    "profit_target_low":          1.0,
    "profit_target_normal":       1.5,
    "profit_target_high":         2.5,
    "max_atr_pct_entry":          6.0,
    "rsi_entry_max":              60,
    "reserve_capital_pct":        0,      # 0 so capital checks don't interfere
    "taker_fee_pct":              0.0,    # 0 so math is clean
    "circuit_breaker_atr_pct":    8.0,
    "max_expansions":             1,
    "expansion_levels":           2,
    "expansion_capital_fraction": 0.3,
}

START = date(2024, 1, 1)


def make_engine(params=None, capital=10_000.0):
    return DcaGridBacktestEngine(
        parameters      = {**BASE_PARAMS, **(params or {})},
        initial_capital = capital,
    )


def make_candle(
    d: date,
    open_: float  = 50_000.0,
    high:  float  = 51_000.0,
    low:   float  = 49_000.0,
    close: float  = 50_000.0,
    volume: float = 1_000_000.0,
) -> BacktestCandle:
    return BacktestCandle(
        date   = d,
        open   = Decimal(str(open_)),
        high   = Decimal(str(high)),
        low    = Decimal(str(low)),
        close  = Decimal(str(close)),
        volume = Decimal(str(volume)),
    )


def make_indicators(
    regime: str   = "normal",
    atr_14: float = 1500.0,
    atr_pct: float = 3.0,
    sma_50:  float | None = 48_000.0,
    sma_200: float | None = 45_000.0,
    rsi_14:  float | None = 45.0,
) -> dict:
    return {
        "volatility_regime": regime,
        "atr_14":  atr_14,
        "atr_pct": atr_pct,
        "sma_50":  sma_50,
        "sma_200": sma_200,
        "rsi_14":  rsi_14,
    }


# ---------------------------------------------------------------------------
# TestCycleOpening
# ---------------------------------------------------------------------------

class TestCycleOpening:

    def test_no_cycle_opened_when_regime_is_high(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), make_indicators(regime="high"))
        assert engine.active_cycle is None

    def test_no_cycle_opened_when_regime_is_extreme(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), make_indicators(regime="extreme"))
        assert engine.active_cycle is None

    def test_no_cycle_opened_when_price_below_sma200(self):
        engine = make_engine()
        ind = make_indicators(sma_200=55_000.0)   # price 50k < sma200 55k
        engine.process_candle(make_candle(START), ind)
        assert engine.active_cycle is None

    def test_no_cycle_opened_when_insufficient_capital(self):
        engine = make_engine(params={"capital_per_cycle": 15_000, "reserve_capital_pct": 0}, capital=10_000)
        engine.process_candle(make_candle(START), make_indicators())
        assert engine.active_cycle is None

    def test_cycle_opened_on_valid_conditions(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), make_indicators())
        assert engine.active_cycle is not None

    def test_cycle_has_correct_level_count(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), make_indicators())
        assert len(engine.active_cycle.levels) == BASE_PARAMS["num_levels"]

    def test_free_capital_reduced_on_open(self):
        engine = make_engine()
        before = engine.free_capital
        engine.process_candle(make_candle(START), make_indicators())
        assert engine.free_capital == before - Decimal(str(BASE_PARAMS["capital_per_cycle"]))

    def test_levels_are_below_reference_price(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        for level in engine.active_cycle.levels:
            assert level.target_price < Decimal("50000")

    def test_equity_recorded_after_candle(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), make_indicators())
        assert len(engine.equity_curve) == 1


# ---------------------------------------------------------------------------
# TestFillSimulation
# ---------------------------------------------------------------------------

class TestFillSimulation:

    def test_level_filled_when_low_touches_target(self):
        engine = make_engine()
        # Day 1: open cycle
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        level1_price = float(cycle.levels[0].target_price)

        # Day 2: low just touches level 1
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=level1_price - 1, high=50_500.0),
            make_indicators(),
        )
        assert cycle.levels[0].status == "FILLED"

    def test_level_not_filled_when_low_above_target(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        level1_price = float(cycle.levels[0].target_price)

        # Day 2: low stays above level 1
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=level1_price + 100, high=50_500.0),
            make_indicators(),
        )
        assert cycle.levels[0].status == "PENDING"

    def test_fill_price_equals_target_price(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        level = cycle.levels[0]

        engine.process_candle(
            make_candle(START + timedelta(days=1), low=float(level.target_price) - 1),
            make_indicators(),
        )
        assert level.fill_price == level.target_price

    def test_multiple_levels_filled_on_deep_drop(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        deepest = float(cycle.levels[-1].target_price)

        # Day 2: low goes below all levels
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=deepest - 100),
            make_indicators(),
        )
        assert all(l.status == "FILLED" for l in cycle.levels)


# ---------------------------------------------------------------------------
# TestTakeProfit
# ---------------------------------------------------------------------------

class TestTakeProfit:

    def _open_and_fill(self, engine, fill_price_offset=100):
        """Open a cycle and fill level 1 on day 2 (high kept below take-profit)."""
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        level = cycle.levels[0]
        # high=49500 stays below even the tightest take-profit (~50019) so the
        # cycle remains OPEN after the fill — tests that want TP fire it explicitly
        engine.process_candle(
            make_candle(START + timedelta(days=1),
                        low=float(level.target_price) - fill_price_offset,
                        high=49_500.0),
            make_indicators(),
        )
        return cycle

    def test_cycle_closed_when_take_profit_hit(self):
        engine = make_engine(params={"profit_target_normal": 2.0, "taker_fee_pct": 0.0})
        cycle  = self._open_and_fill(engine)
        avg    = float(cycle.avg_entry())
        tp     = avg * 1.02   # 2% above avg entry

        engine.process_candle(
            make_candle(START + timedelta(days=2), low=avg - 100, high=tp + 100),
            make_indicators(),
        )
        assert cycle.status == "CLOSED"
        assert cycle.close_trigger == "take_profit"

    def test_cycle_not_closed_when_high_below_take_profit(self):
        engine = make_engine(params={"profit_target_normal": 5.0, "taker_fee_pct": 0.0})
        cycle  = self._open_and_fill(engine)
        # high barely above avg entry but far below 5% take-profit
        engine.process_candle(
            make_candle(START + timedelta(days=2), high=float(cycle.avg_entry()) + 10),
            make_indicators(),
        )
        assert cycle.status == "OPEN"

    def test_take_profit_pnl_is_positive(self):
        engine = make_engine(params={"profit_target_normal": 2.0, "taker_fee_pct": 0.0})
        cycle  = self._open_and_fill(engine)
        avg    = float(cycle.avg_entry())
        tp     = avg * 1.02

        engine.process_candle(
            make_candle(START + timedelta(days=2), low=avg - 100, high=tp + 200),
            make_indicators(),
        )
        assert float(cycle.net_pnl()) > 0

    def test_free_capital_increases_after_take_profit(self):
        engine = make_engine(params={"profit_target_normal": 2.0, "taker_fee_pct": 0.0})
        initial_free = engine.free_capital
        self._open_and_fill(engine)
        after_fill = engine.free_capital

        cycle = engine.closed_cycles[0] if engine.closed_cycles else engine.active_cycle
        avg   = float(cycle.avg_entry()) if engine.active_cycle else float(engine.closed_cycles[-1].avg_entry())

        if engine.active_cycle:
            cycle = engine.active_cycle
            tp    = float(cycle.avg_entry()) * 1.02
            engine.process_candle(
                make_candle(START + timedelta(days=2), low=float(cycle.avg_entry()) - 100, high=tp + 100),
                make_indicators(),
            )

        assert engine.free_capital > after_fill


# ---------------------------------------------------------------------------
# TestStopLoss
# ---------------------------------------------------------------------------

class TestStopLoss:

    def test_cycle_closed_on_stop_loss(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle      = engine.active_cycle
        stop_price = float(cycle.stop_loss_price)

        engine.process_candle(
            make_candle(START + timedelta(days=1), low=stop_price - 100),
            make_indicators(),
        )
        assert cycle.status == "CLOSED"
        assert cycle.close_trigger == "stop_loss"

    def test_stop_loss_pnl_is_negative_when_fills_exist(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle  = engine.active_cycle
        level  = cycle.levels[0]

        # Fill level 1 first (high kept below take-profit so cycle stays OPEN)
        engine.process_candle(
            make_candle(START + timedelta(days=1),
                        low=float(level.target_price) - 100,
                        high=49_500.0),
            make_indicators(),
        )
        stop_price = float(cycle.stop_loss_price)
        # Then stop-loss fires
        engine.process_candle(
            make_candle(START + timedelta(days=2), low=stop_price - 100, open_=stop_price - 50),
            make_indicators(),
        )
        assert float(cycle.net_pnl()) < 0

    def test_stop_loss_close_price_honours_gap_risk(self):
        """If candle opens below stop-loss, fill at open (not at stop price)."""
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle      = engine.active_cycle
        stop_price = float(cycle.stop_loss_price)

        # Candle opens BELOW stop-loss (gap down)
        gap_open = stop_price - 200
        engine.process_candle(
            make_candle(START + timedelta(days=1), open_=gap_open, low=gap_open - 100),
            make_indicators(),
        )
        assert float(cycle.close_price) == pytest.approx(gap_open, rel=1e-6)


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:

    def test_circuit_breaker_fires_on_extreme_regime(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle

        engine.process_candle(
            make_candle(START + timedelta(days=1)),
            make_indicators(regime="extreme"),
        )
        assert cycle.status == "CLOSED"
        assert cycle.close_trigger == "circuit_breaker"

    def test_circuit_breaker_fires_when_atr_exceeds_threshold(self):
        engine = make_engine(params={"circuit_breaker_atr_pct": 5.0})
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle

        engine.process_candle(
            make_candle(START + timedelta(days=1)),
            make_indicators(regime="normal", atr_pct=6.0),   # above threshold 5.0
        )
        assert cycle.status == "CLOSED"
        assert cycle.close_trigger == "circuit_breaker"

    def test_circuit_breaker_does_not_fire_below_threshold(self):
        engine = make_engine(params={"circuit_breaker_atr_pct": 8.0})
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle

        # low=50200 is above all level targets so no fills and no take-profit
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=50_200.0, high=50_500.0),
            make_indicators(regime="normal", atr_pct=3.0),
        )
        assert cycle.status == "OPEN"


# ---------------------------------------------------------------------------
# TestAutoTune
# ---------------------------------------------------------------------------

class TestAutoTune:

    def test_profit_target_updated_on_regime_change(self):
        engine = make_engine()
        # Open cycle in normal regime (profit_target = 1.5)
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators(regime="normal"))
        cycle = engine.active_cycle
        assert cycle.profit_target_pct == Decimal("1.5")

        # Regime shifts to low (profit_target should become 1.0)
        engine.process_candle(
            make_candle(START + timedelta(days=1)),
            make_indicators(regime="low"),
        )
        assert cycle.profit_target_pct == Decimal("1.0")

    def test_pending_levels_repriced_on_regime_change(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators(regime="normal"))
        cycle = engine.active_cycle
        original_prices = [l.target_price for l in cycle.pending_levels]

        engine.process_candle(
            make_candle(START + timedelta(days=1)),
            make_indicators(regime="low", atr_pct=1.0),   # much tighter spacing
        )
        new_prices = [l.target_price for l in cycle.pending_levels]
        assert new_prices != original_prices

    def test_no_retune_into_high_or_extreme(self):
        engine = make_engine()
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators(regime="normal"))
        cycle = engine.active_cycle
        original_target = cycle.profit_target_pct

        # Regime shifts to high — retune skipped, circuit breaker will handle
        engine.process_candle(
            make_candle(START + timedelta(days=1)),
            make_indicators(regime="high", atr_pct=4.0),  # below cb_threshold=8
        )
        # profit_target should be unchanged (retune skipped for high)
        assert cycle.profit_target_pct == original_target


# ---------------------------------------------------------------------------
# TestGridExpansion
# ---------------------------------------------------------------------------

class TestGridExpansion:

    def _open_and_fill_all(self, engine):
        """Open a cycle and fill ALL levels on day 2 (high kept below take-profit)."""
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        deepest = float(cycle.levels[-1].target_price)
        # high=48000 is below the weighted avg take-profit (~48969) so cycle stays OPEN
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=deepest - 500, high=48_000.0),
            make_indicators(),
        )
        return cycle

    def test_expansion_levels_added_when_all_filled(self):
        engine = make_engine(params={"expansion_levels": 2})
        cycle  = self._open_and_fill_all(engine)
        initial_count = BASE_PARAMS["num_levels"]

        # Day 3: no pending remain → expansion should fire
        # high=48500 is below target_sell (~48969) so take-profit does not fire first
        engine.process_candle(
            make_candle(START + timedelta(days=2), high=48_500.0),
            make_indicators(),
        )
        assert len(cycle.levels) == initial_count + 2

    def test_expansion_levels_have_crash_expansion_trigger(self):
        engine = make_engine()
        cycle  = self._open_and_fill_all(engine)
        engine.process_candle(
            make_candle(START + timedelta(days=2), high=48_500.0),
            make_indicators(),
        )
        crash_levels = [l for l in cycle.levels if l.level_trigger == "crash_expansion"]
        assert len(crash_levels) == BASE_PARAMS["expansion_levels"]

    def test_expansion_not_repeated_beyond_max(self):
        engine = make_engine(params={"max_expansions": 1, "expansion_levels": 2})
        cycle  = self._open_and_fill_all(engine)

        # Day 3: first expansion fires
        engine.process_candle(
            make_candle(START + timedelta(days=2), high=48_500.0),
            make_indicators(),
        )
        # Fill the expansion levels too
        deepest = float(min(l.target_price for l in cycle.pending_levels))
        engine.process_candle(
            make_candle(START + timedelta(days=3), low=deepest - 500),
            make_indicators(),
        )
        count_after_first_expansion = len(cycle.levels)

        # Day 5: max reached — no more expansion
        engine.process_candle(make_candle(START + timedelta(days=4)), make_indicators())
        assert len(cycle.levels) == count_after_first_expansion


# ---------------------------------------------------------------------------
# TestMetrics
# ---------------------------------------------------------------------------

class TestMetrics:

    def _run_simple_cycle(self, profit_pct=2.0):
        """Run one complete profitable cycle and return metrics."""
        engine = make_engine(params={"profit_target_normal": profit_pct, "taker_fee_pct": 0.0, "num_levels": 1, "weights": [1]})
        # Day 1: open
        engine.process_candle(make_candle(START, close=50_000.0), make_indicators())
        cycle = engine.active_cycle
        level = cycle.levels[0]
        # Day 2: fill
        engine.process_candle(
            make_candle(START + timedelta(days=1), low=float(level.target_price) - 100),
            make_indicators(),
        )
        # Day 3: take-profit
        tp = float(cycle.avg_entry()) * (1 + profit_pct / 100)
        engine.process_candle(
            make_candle(START + timedelta(days=2), high=tp + 100),
            make_indicators(),
        )
        return engine.compute_metrics()

    def test_total_cycles_counts_closed_cycles(self):
        metrics = self._run_simple_cycle()
        assert metrics["total_cycles"] == 1

    def test_win_rate_100_pct_when_all_cycles_profitable(self):
        metrics = self._run_simple_cycle()
        assert metrics["win_rate"] == pytest.approx(100.0, rel=0.01)

    def test_total_return_pct_positive_after_profitable_cycle(self):
        metrics = self._run_simple_cycle()
        assert metrics["total_return_pct"] > 0

    def test_max_drawdown_is_non_negative(self):
        metrics = self._run_simple_cycle()
        assert metrics["max_drawdown_pct"] >= 0

    def test_avg_cycle_duration_secs_positive(self):
        metrics = self._run_simple_cycle()
        assert metrics["avg_cycle_duration_secs"] > 0

    def test_no_indicators_candle_still_recorded_in_equity_curve(self):
        engine = make_engine()
        engine.process_candle(make_candle(START), None)   # no indicators
        assert len(engine.equity_curve) == 1
        assert engine.active_cycle is None

    def test_equity_equals_initial_capital_before_any_cycle(self):
        engine = make_engine(capital=10_000.0)
        # Feed candles that don't meet entry conditions
        engine.process_candle(make_candle(START), make_indicators(regime="high"))
        _, equity = engine.equity_curve[-1]
        assert equity == Decimal("10000")
