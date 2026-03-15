"""
Unit tests for TrendFollowingStrategy.

Pure method tests (no I/O):
  - _check_entry_conditions
  - _compute_atr
  - _compute_rsi

Intraday guard tests (mocked exchange):
  - _load_intraday_atr (live ATR from exchange candles)
  - _load_intraday_rsi (live RSI from exchange candles)

Paper mode tests (mocked DB):
  - _open_cycle in paper mode (no trade_orders)
  - _close_position in paper mode (no exchange sell, no trade_orders)
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from bots.trader_bot.strategies.trend_following import TrendFollowingStrategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy():
    return TrendFollowingStrategy()


@pytest.fixture
def base_meta():
    return {
        "capital_allocated":    1000,
        "risk_pct_per_trade":   1.0,
        "atr_stop_multiplier":  2.0,
        "atr_trail_multiplier": 3.0,
        "min_adx":              25.0,
        "min_regime_score":     61.0,
        "rsi_entry_max":        70.0,
        "max_atr_pct_entry":    6.0,
        "reserve_capital_pct":  20,
    }


class _FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


def make_mock_conn():
    conn = AsyncMock()
    conn.execute     = AsyncMock()
    conn.fetchval    = AsyncMock()
    conn.fetchrow    = AsyncMock()
    conn.fetch       = AsyncMock(return_value=[])
    conn.transaction = lambda: _FakeTransaction()
    return conn


# ---------------------------------------------------------------------------
# _check_entry_conditions
# ---------------------------------------------------------------------------

class TestCheckEntryConditions:

    def _make_indicators(self, **overrides):
        base = {
            "atr_14": 1200, "atr_pct": 2.5, "ema_50": 49000,
            "ema_200": 45000, "adx_14": 30, "rsi_14": 55,
            "ema_slope_5d": 0.5, "vol_ratio_14": 1.2,
        }
        base.update(overrides)
        return base

    def test_all_conditions_pass(self, strategy, base_meta):
        indicators = self._make_indicators()
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is True

    def test_rejects_low_regime_score(self, strategy, base_meta):
        indicators = self._make_indicators()
        regime = {"final_regime_score": 40.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is False
        assert "not trending" in reason

    def test_rejects_no_golden_cross(self, strategy, base_meta):
        indicators = self._make_indicators(ema_50=44000, ema_200=45000)
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is False
        assert "golden cross" in reason

    def test_rejects_price_below_5d_high(self, strategy, base_meta):
        indicators = self._make_indicators()
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("49000"), high_5d=50000.0,
        )
        assert passed is False
        assert "5d-high" in reason

    def test_rejects_weak_adx(self, strategy, base_meta):
        indicators = self._make_indicators(adx_14=20)
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is False
        assert "weak trend" in reason

    def test_rejects_overbought_rsi(self, strategy, base_meta):
        indicators = self._make_indicators(rsi_14=75)
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is False
        assert "overbought" in reason

    def test_rejects_extreme_volatility(self, strategy, base_meta):
        indicators = self._make_indicators(atr_pct=7.0)
        regime = {"final_regime_score": 65.0}
        passed, reason = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is False
        assert "extreme volatility" in reason

    def test_passes_with_none_rsi(self, strategy, base_meta):
        """RSI check is skipped when rsi_14 is None."""
        indicators = self._make_indicators(rsi_14=None)
        regime = {"final_regime_score": 65.0}
        passed, _ = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is True

    def test_passes_with_none_atr_pct(self, strategy, base_meta):
        """ATR% check is skipped when atr_pct is None."""
        indicators = self._make_indicators(atr_pct=None)
        regime = {"final_regime_score": 65.0}
        passed, _ = strategy._check_entry_conditions(
            base_meta, indicators, regime,
            current_price=Decimal("50000"), high_5d=49500.0,
        )
        assert passed is True


# ---------------------------------------------------------------------------
# Paper mode: _open_cycle
# ---------------------------------------------------------------------------

class TestOpenCyclePaper:

    @pytest.mark.asyncio
    async def test_paper_mode_skips_trade_orders(self, strategy):
        """In paper mode, cycle is created but trade_orders INSERT is skipped."""
        strategy._paper = True
        conn = make_mock_conn()
        conn.fetchval = AsyncMock(side_effect=[1, 100])  # cycle_number, cycle_id

        strat = {
            "id": 5, "venue_id": 1, "quote_asset_id": 7,
            "base_asset_code": "BTC", "quote_asset_code": "USDT",
        }

        await strategy._open_cycle(
            conn=conn,
            strategy=strat,
            entry_price=Decimal("50000"),
            position_size=Decimal("0.02"),
            capital_allocated=Decimal("1000"),
            initial_stop_loss=Decimal("47600"),
            atr_at_entry=Decimal("1200"),
            high_5d_at_entry=Decimal("49500"),
            exchange_order_id="",
        )

        # Check that no trade_orders INSERT was called
        all_sqls = [str(c) for c in conn.execute.call_args_list]
        trade_order_calls = [s for s in all_sqls if "trade_orders" in s]
        assert len(trade_order_calls) == 0

        # But system_events should contain [PAPER]
        system_event_calls = [s for s in all_sqls if "system_events" in s]
        assert len(system_event_calls) > 0

    @pytest.mark.asyncio
    async def test_live_mode_writes_trade_orders(self, strategy):
        """In live mode, trade_orders INSERT is present."""
        strategy._paper = False
        conn = make_mock_conn()
        conn.fetchval = AsyncMock(side_effect=[1, 100])

        strat = {
            "id": 5, "venue_id": 1, "quote_asset_id": 7,
            "base_asset_code": "BTC", "quote_asset_code": "USDT",
        }

        await strategy._open_cycle(
            conn=conn,
            strategy=strat,
            entry_price=Decimal("50000"),
            position_size=Decimal("0.02"),
            capital_allocated=Decimal("1000"),
            initial_stop_loss=Decimal("47600"),
            atr_at_entry=Decimal("1200"),
            high_5d_at_entry=Decimal("49500"),
            exchange_order_id="order-abc",
        )

        all_sqls = [str(c) for c in conn.execute.call_args_list]
        trade_order_calls = [s for s in all_sqls if "trade_orders" in s]
        assert len(trade_order_calls) > 0


# ---------------------------------------------------------------------------
# Paper mode: _close_position
# ---------------------------------------------------------------------------

class TestClosePositionPaper:

    @pytest.mark.asyncio
    async def test_paper_mode_skips_exchange_sell(self, strategy):
        """In paper mode, no exchange sell order and no trade_orders INSERT."""
        strategy._paper = True
        conn = make_mock_conn()
        exchange = AsyncMock()

        strat = {
            "id": 5, "venue_id": 1, "quote_asset_id": 7,
            "base_asset_code": "BTC", "quote_asset_code": "USDT",
        }
        cycle = {"id": 10, "cycle_number": 1}
        cycle_meta = {
            "entry_price": 50000.0,
            "position_size": 0.02,
            "initial_stop_loss": 47600.0,
            "highest_price_since_entry": 52000.0,
        }

        await strategy._close_position(
            exchange, conn, strat, cycle, cycle_meta,
            current_price=Decimal("48000"),
            position_size=Decimal("0.02"),
            effective_stop=Decimal("48000"),
            trigger="trailing_stop",
        )

        # Exchange should NOT be called
        exchange.create_order.assert_not_awaited()

        # trade_orders should NOT be written
        all_sqls = [str(c) for c in conn.execute.call_args_list]
        trade_order_calls = [s for s in all_sqls if "trade_orders" in s]
        assert len(trade_order_calls) == 0

        # Cycle should be CLOSED
        close_calls = [s for s in all_sqls if "CLOSED" in s]
        assert len(close_calls) > 0

    @pytest.mark.asyncio
    async def test_live_mode_places_exchange_sell(self, strategy):
        """In live mode, exchange sell is placed and trade_orders is written."""
        strategy._paper = False
        conn = make_mock_conn()
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={"id": "sell-xyz", "price": 48000})

        strat = {
            "id": 5, "venue_id": 1, "quote_asset_id": 7,
            "base_asset_code": "BTC", "quote_asset_code": "USDT",
        }
        cycle = {"id": 10, "cycle_number": 1}
        cycle_meta = {
            "entry_price": 50000.0,
            "position_size": 0.02,
            "initial_stop_loss": 47600.0,
            "highest_price_since_entry": 52000.0,
        }

        await strategy._close_position(
            exchange, conn, strat, cycle, cycle_meta,
            current_price=Decimal("48000"),
            position_size=Decimal("0.02"),
            effective_stop=Decimal("48000"),
            trigger="trailing_stop",
        )

        exchange.create_order.assert_awaited_once()

        all_sqls = [str(c) for c in conn.execute.call_args_list]
        trade_order_calls = [s for s in all_sqls if "trade_orders" in s]
        assert len(trade_order_calls) > 0


# ---------------------------------------------------------------------------
# _compute_atr (pure method)
# ---------------------------------------------------------------------------

class TestComputeAtr:

    def _make_candles(self, closes, spread=200):
        """Build candle dicts from close prices."""
        candles = []
        for c in closes:
            candles.append({
                "high": c + spread,
                "low": c - spread,
                "close": c,
            })
        return candles

    def test_basic_atr(self, strategy):
        # 16 candles → 15 TRs → enough for period=14
        closes = [50000 + i * 100 for i in range(16)]
        candles = self._make_candles(closes)
        atr = strategy._compute_atr(candles, period=14)
        assert atr is not None
        assert atr > 0

    def test_insufficient_candles_returns_none(self, strategy):
        candles = self._make_candles([50000, 51000])
        assert strategy._compute_atr(candles, period=14) is None

    def test_stable_prices_low_atr(self, strategy):
        """Nearly flat prices → ATR is close to the candle spread."""
        candles = self._make_candles([50000] * 16, spread=100)
        atr = strategy._compute_atr(candles, period=14)
        assert atr is not None
        # With 0 close-to-close change and spread=100, TR≈200 (high-low)
        assert abs(atr - 200) < 50

    def test_volatile_prices_high_atr(self, strategy):
        # Alternating large moves
        closes = [50000 + ((-1)**i) * 1000 for i in range(16)]
        candles = self._make_candles(closes, spread=200)
        atr = strategy._compute_atr(candles, period=14)
        assert atr is not None
        assert atr > 1000  # should capture the big swings


# ---------------------------------------------------------------------------
# _compute_rsi (pure method)
# ---------------------------------------------------------------------------

class TestComputeRsi:

    def test_basic_rsi(self, strategy):
        closes = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        rsi = strategy._compute_rsi(closes, period=14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_insufficient_data_returns_none(self, strategy):
        assert strategy._compute_rsi([50000, 51000], period=14) is None

    def test_all_gains_returns_100(self, strategy):
        closes = [float(i) for i in range(100, 116)]
        rsi = strategy._compute_rsi(closes, period=14)
        assert rsi == 100.0

    def test_all_losses_returns_0(self, strategy):
        closes = [float(i) for i in range(200, 184, -1)]
        rsi = strategy._compute_rsi(closes, period=14)
        assert rsi == 0.0


# ---------------------------------------------------------------------------
# _load_intraday_atr (exchange candles → live ATR)
# ---------------------------------------------------------------------------

class TestLoadIntradayAtr:

    @pytest.mark.asyncio
    async def test_returns_atr_from_exchange_candles(self, strategy):
        """Live ATR computed from exchange 1h OHLCV."""
        candles = [
            {"high": 50200 + i*50, "low": 49800 + i*50,
             "close": 50000 + i*50, "open": 50000 + i*50, "volume": 10}
            for i in range(29)
        ]
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=candles)

        atr = await strategy._load_intraday_atr(exchange, "BTC/USDT")
        assert atr is not None
        assert float(atr) > 0

    @pytest.mark.asyncio
    async def test_returns_none_on_insufficient_candles(self, strategy):
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=[
            {"high": 50200, "low": 49800, "close": 50000}
        ])

        atr = await strategy._load_intraday_atr(exchange, "BTC/USDT")
        assert atr is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exchange_error(self, strategy):
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("timeout"))

        atr = await strategy._load_intraday_atr(exchange, "BTC/USDT")
        assert atr is None


# ---------------------------------------------------------------------------
# _load_intraday_rsi (exchange candles → live RSI)
# ---------------------------------------------------------------------------

class TestLoadIntradayRsi:

    @pytest.mark.asyncio
    async def test_returns_rsi_from_exchange_candles(self, strategy):
        """Live RSI computed from exchange 1h OHLCV."""
        # Alternating up/down to get mid-range RSI
        candles = [
            {"high": 50200, "low": 49800,
             "close": 50000 + ((-1)**i) * 100, "open": 50000, "volume": 10}
            for i in range(29)
        ]
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=candles)

        rsi = await strategy._load_intraday_rsi(exchange, "BTC/USDT")
        assert rsi is not None
        assert 0 <= rsi <= 100

    @pytest.mark.asyncio
    async def test_returns_none_on_exchange_error(self, strategy):
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("timeout"))

        rsi = await strategy._load_intraday_rsi(exchange, "BTC/USDT")
        assert rsi is None
