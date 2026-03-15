"""
Unit tests for DcaGridStrategy.

Pure method tests (no I/O):
  - _check_entry_conditions
  - _check_defensive_entry
  - _compute_grid_levels
  - _avg_entry
  - _compute_rsi

Paper mode tests (mocked DB):
  - _place_grid_buy in paper mode (instant fill, no trade_orders)
  - _close_cycle in paper mode (direct finalize, no exchange sell)
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from bots.trader_bot.strategies.dca_grid import DcaGridStrategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy():
    return DcaGridStrategy()


@pytest.fixture
def base_meta():
    return {
        "capital_per_cycle":     1000,
        "num_levels":            5,
        "weights":               [1, 1, 2, 3, 3],
        "atr_multiplier_low":    0.4,
        "atr_multiplier_normal": 0.5,
        "atr_multiplier_high":   0.7,
        "profit_target_low":     1.0,
        "profit_target_normal":  1.5,
        "profit_target_high":    2.5,
        "max_atr_pct_entry":     6.0,
        "rsi_entry_max":         60,
        "reserve_capital_pct":   30,
    }


class _FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


def make_mock_conn():
    conn = AsyncMock()
    conn.execute     = AsyncMock()
    conn.executemany = AsyncMock()
    conn.fetchval    = AsyncMock()
    conn.fetchrow    = AsyncMock()
    conn.fetch       = AsyncMock(return_value=[])
    conn.transaction = lambda: _FakeTransaction()
    return conn


# ---------------------------------------------------------------------------
# _check_entry_conditions
# ---------------------------------------------------------------------------

class TestCheckEntryConditions:

    def test_all_conditions_pass(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is True
        assert "all conditions passed" in reason

    def test_rejects_high_volatility_regime(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="high",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is False
        assert "volatility_regime" in reason

    def test_rejects_extreme_volatility_regime(self, strategy, base_meta):
        passed, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="extreme",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=None, sma_200=None, rsi_14=None,
        )
        assert passed is False

    def test_rejects_atr_above_max(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("7.0"),   # above max_atr_pct_entry=6.0
            current_price=Decimal("50000"),
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is False
        assert "atr_pct" in reason

    def test_rejects_price_below_sma200(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("40000"),  # below sma_200
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is False
        assert "downtrend" in reason

    def test_rejects_death_cross(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=Decimal("44000"),   # below sma_200
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is False
        assert "death cross" in reason

    def test_rejects_high_rsi(self, strategy, base_meta):
        passed, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("65"),    # above rsi_entry_max=60
        )
        assert passed is False
        assert "rsi_14" in reason

    def test_passes_when_sma_is_none(self, strategy, base_meta):
        """When SMA data is unavailable, uptrend/golden cross checks are skipped."""
        passed, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=None,
            rsi_14=Decimal("45"),
        )
        assert passed is True

    def test_require_uptrend_disabled(self, strategy, base_meta):
        meta = {**base_meta, "require_uptrend": False}
        passed, _ = strategy._check_entry_conditions(
            meta=meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.5"),
            current_price=Decimal("40000"),  # below sma_200
            sma_50=Decimal("48000"),
            sma_200=Decimal("45000"),
            rsi_14=Decimal("45"),
        )
        assert passed is True


# ---------------------------------------------------------------------------
# _check_defensive_entry
# ---------------------------------------------------------------------------

class TestCheckDefensiveEntry:

    def test_bounce_signal_when_oversold_in_downtrend(self, strategy, base_meta):
        meta = {**base_meta, "defensive_rsi_oversold": 40}
        indicators = {"sma_200": 52000, "sma_50": 48000, "rsi_14": 35}
        passed, reason = strategy._check_defensive_entry(
            meta, indicators, current_price=Decimal("45000"), intraday_rsi=32.0,
        )
        assert passed is True
        assert "oversold bounce" in reason

    def test_no_bounce_when_rsi_above_threshold(self, strategy, base_meta):
        meta = {**base_meta, "defensive_rsi_oversold": 40}
        indicators = {"sma_200": 52000, "sma_50": 48000, "rsi_14": 55}
        passed, _ = strategy._check_defensive_entry(
            meta, indicators, current_price=Decimal("45000"), intraday_rsi=55.0,
        )
        assert passed is False

    def test_not_applicable_when_not_in_downtrend(self, strategy, base_meta):
        meta = {**base_meta, "defensive_rsi_oversold": 40}
        indicators = {"sma_200": 45000, "sma_50": 48000, "rsi_14": 35}
        passed, reason = strategy._check_defensive_entry(
            meta, indicators, current_price=Decimal("50000"),
        )
        assert passed is False
        assert "not in downtrend" in reason


# ---------------------------------------------------------------------------
# _compute_grid_levels
# ---------------------------------------------------------------------------

class TestComputeGridLevels:

    def test_correct_number_of_levels(self, strategy):
        levels = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=5,
            weights=[1, 1, 2, 3, 3],
            capital_per_cycle=Decimal("1000"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
        )
        assert len(levels) == 5

    def test_levels_descend_in_price(self, strategy):
        levels = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=5,
            weights=[1, 1, 2, 3, 3],
            capital_per_cycle=Decimal("1000"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
        )
        prices = [l["target_price"] for l in levels]
        for i in range(len(prices) - 1):
            assert prices[i] > prices[i + 1]

    def test_capital_sums_to_total(self, strategy):
        levels = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=3,
            weights=[1, 2, 3],
            capital_per_cycle=Decimal("600"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
        )
        total = sum(l["capital_allocated"] for l in levels)
        assert total == pytest.approx(float(Decimal("600")), abs=0.01)

    def test_maker_fee_reduces_quantity(self, strategy):
        levels_no_fee = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=1, weights=[1],
            capital_per_cycle=Decimal("1000"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
            maker_fee_pct=Decimal("0"),
        )
        levels_with_fee = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=1, weights=[1],
            capital_per_cycle=Decimal("1000"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
            maker_fee_pct=Decimal("0.0025"),
        )
        assert levels_with_fee[0]["quantity"] < levels_no_fee[0]["quantity"]

    def test_level_trigger_is_initial(self, strategy):
        levels = strategy._compute_grid_levels(
            reference_price=Decimal("50000"),
            grid_spacing_pct=Decimal("1.5"),
            num_levels=2, weights=[1, 1],
            capital_per_cycle=Decimal("1000"),
            atr_value=Decimal("1500"),
            atr_multiplier=Decimal("0.5"),
        )
        assert all(l["level_trigger"] == "initial" for l in levels)


# ---------------------------------------------------------------------------
# _avg_entry
# ---------------------------------------------------------------------------

class TestAvgEntry:

    def test_single_level(self, strategy):
        levels = [{"target_price": "50000", "quantity": "0.01"}]
        avg = strategy._avg_entry(levels)
        assert avg == Decimal("50000")

    def test_weighted_average(self, strategy):
        levels = [
            {"target_price": "50000", "quantity": "0.01"},
            {"target_price": "48000", "quantity": "0.02"},
        ]
        avg = strategy._avg_entry(levels)
        expected = (Decimal("50000") * Decimal("0.01") + Decimal("48000") * Decimal("0.02")) / Decimal("0.03")
        assert avg == expected

    def test_empty_levels_returns_zero(self, strategy):
        assert strategy._avg_entry([]) == Decimal("0")


# ---------------------------------------------------------------------------
# _compute_rsi
# ---------------------------------------------------------------------------

class TestComputeRsi:

    def test_basic_rsi(self):
        # 15 closes = 14 changes → RSI seed from first 14 changes
        closes = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        rsi = DcaGridStrategy._compute_rsi(closes, period=14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_insufficient_data_returns_none(self):
        assert DcaGridStrategy._compute_rsi([50000, 51000], period=14) is None

    def test_all_gains_returns_100(self):
        closes = [float(i) for i in range(100, 116)]  # 16 values, all rising
        rsi = DcaGridStrategy._compute_rsi(closes, period=14)
        assert rsi == 100.0

    def test_all_losses_returns_0(self):
        closes = [float(i) for i in range(200, 184, -1)]  # 16 values, all falling
        rsi = DcaGridStrategy._compute_rsi(closes, period=14)
        assert rsi == 0.0


# ---------------------------------------------------------------------------
# Paper mode: _place_grid_buy
# ---------------------------------------------------------------------------

class TestPlaceGridBuyPaper:

    @pytest.mark.asyncio
    async def test_paper_mode_marks_level_filled_directly(self, strategy):
        """In paper mode, grid level goes PENDING→FILLED with no trade_orders."""
        strategy._paper = True
        conn = make_mock_conn()
        exchange = AsyncMock()

        level = {"id": 42, "level_num": 1, "target_price": "49000", "quantity": "0.02"}
        strat = {"id": 1, "base_asset_code": "BTC", "quote_asset_code": "USDT"}
        cycle = {"id": 10}

        await strategy._place_grid_buy(exchange, conn, strat, cycle, level)

        # Exchange should NOT have been called
        exchange.create_order.assert_not_awaited()

        # Grid level should be marked FILLED
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "FILLED" in sql
        assert conn.execute.call_args[0][1] == 42  # level id

    @pytest.mark.asyncio
    async def test_live_mode_places_order_on_exchange(self, strategy):
        """In live mode, grid level goes PENDING→OPEN via exchange order."""
        strategy._paper = False
        conn = make_mock_conn()
        conn.fetchval = AsyncMock(return_value=99)  # order_id
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={"id": "exch-123"})

        level = {"id": 42, "level_num": 1, "target_price": "49000", "quantity": "0.02"}
        strat = {"id": 1, "base_asset_code": "BTC", "quote_asset_code": "USDT"}
        cycle = {"id": 10}

        await strategy._place_grid_buy(exchange, conn, strat, cycle, level)

        exchange.create_order.assert_awaited_once()


# ---------------------------------------------------------------------------
# Paper mode: _close_cycle
# ---------------------------------------------------------------------------

class TestCloseCyclePaper:

    @pytest.mark.asyncio
    async def test_paper_mode_calls_finalize_paper(self, strategy):
        """In paper mode, _close_cycle should call _finalize_closed_cycle_paper."""
        strategy._paper = True

        conn = make_mock_conn()
        # Mock the price observation query for paper finalize
        conn.fetchrow = AsyncMock(return_value={"observed_price": "51000"})

        exchange = AsyncMock()
        strat = {
            "id": 1, "base_asset_code": "BTC", "quote_asset_code": "USDT",
            "base_asset_id": 1, "quote_asset_id": 7,
            "maker_fee_pct": 0.0025, "taker_fee_pct": 0.005,
        }
        cycle = {
            "id": 10, "cycle_number": 1,
            "opened_at": datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc),
        }
        filled_levels = [
            {"target_price": "49000", "quantity": "0.01"},
            {"target_price": "48000", "quantity": "0.02"},
        ]

        await strategy._close_cycle(exchange, conn, strat, cycle, filled_levels, "take_profit")

        # Exchange sell should NOT be called
        exchange.create_order.assert_not_awaited()

        # System event should be written with [PAPER] prefix
        calls = conn.execute.call_args_list
        system_event_calls = [c for c in calls if "system_events" in str(c)]
        assert len(system_event_calls) > 0

    @pytest.mark.asyncio
    async def test_live_mode_places_sell_order(self, strategy):
        """In live mode, _close_cycle places exchange sell and goes to CLOSING."""
        strategy._paper = False
        conn = make_mock_conn()
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={"id": "sell-123"})

        strat = {"id": 1, "base_asset_code": "BTC", "quote_asset_code": "USDT"}
        cycle = {"id": 10, "cycle_number": 1}
        filled_levels = [{"target_price": "49000", "quantity": "0.01"}]

        await strategy._close_cycle(exchange, conn, strat, cycle, filled_levels, "take_profit")

        exchange.create_order.assert_awaited_once()
        # Cycle should move to CLOSING
        closing_calls = [c for c in conn.execute.call_args_list if "CLOSING" in str(c)]
        assert len(closing_calls) > 0


# ---------------------------------------------------------------------------
# _is_intraday_volatility_elevated
# ---------------------------------------------------------------------------

class TestIntradayVolatilityGuard:

    @pytest.mark.asyncio
    async def test_returns_true_when_range_exceeds_atr(self, strategy):
        """Intraday range > daily ATR → guard fires."""
        conn = make_mock_conn()
        # Load indicators returns ATR=1200
        conn.fetchrow = AsyncMock(side_effect=[
            {"atr_14": 1200, "atr_pct": 2.5, "atr_sma_20": 1100,
             "volatility_regime": "normal", "sma_50": 49000,
             "sma_200": 45000, "rsi_14": 50, "metric_date": "2026-03-14"},
            # Intraday range query: MAX-MIN = 1500 > ATR=1200
            {"intraday_range": 1500},
        ])

        strat = {"base_asset_id": 1, "quote_asset_id": 7}
        result = await strategy._is_intraday_volatility_elevated(conn, strat)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_range_within_atr(self, strategy):
        """Intraday range < daily ATR → guard doesn't fire."""
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            {"atr_14": 1200, "atr_pct": 2.5, "atr_sma_20": 1100,
             "volatility_regime": "normal", "sma_50": 49000,
             "sma_200": 45000, "rsi_14": 50, "metric_date": "2026-03-14"},
            {"intraday_range": 800},
        ])

        strat = {"base_asset_id": 1, "quote_asset_id": 7}
        result = await strategy._is_intraday_volatility_elevated(conn, strat)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_observations(self, strategy):
        """No price observations → guard doesn't fire (safe default)."""
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            {"atr_14": 1200, "atr_pct": 2.5, "atr_sma_20": 1100,
             "volatility_regime": "normal", "sma_50": 49000,
             "sma_200": 45000, "rsi_14": 50, "metric_date": "2026-03-14"},
            {"intraday_range": None},
        ])

        strat = {"base_asset_id": 1, "quote_asset_id": 7}
        result = await strategy._is_intraday_volatility_elevated(conn, strat)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_indicators(self, strategy):
        """No indicator data → guard doesn't fire."""
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        strat = {"base_asset_id": 1, "quote_asset_id": 7}
        result = await strategy._is_intraday_volatility_elevated(conn, strat)
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_provided_indicators_cache(self, strategy):
        """When indicators_cache is provided, no DB query for indicators."""
        conn = make_mock_conn()
        # Only the range query should be called
        conn.fetchrow = AsyncMock(return_value={"intraday_range": 1500})

        indicators = {"atr_14": 1200}
        strat = {"base_asset_id": 1, "quote_asset_id": 7}
        result = await strategy._is_intraday_volatility_elevated(
            conn, strat, indicators_cache=indicators,
        )
        assert result is True
        # Only one fetchrow call (range query), not two
        assert conn.fetchrow.await_count == 1
