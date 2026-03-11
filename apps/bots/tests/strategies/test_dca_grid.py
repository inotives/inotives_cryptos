"""
Unit tests for DcaGridStrategy pure methods and fill-polling logic.

Pure method tests (no I/O):
  - _check_entry_conditions
  - _compute_grid_levels
  - _avg_entry

Fill polling tests (mocked exchange + DB connection):
  - _poll_open_orders
  - _record_fill
  - _poll_closing_cycles
  - _finalize_closed_cycle
  - _maybe_retune
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from trader_bot.strategies.dca_grid import DcaGridStrategy

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy():
    return DcaGridStrategy()


@pytest.fixture
def base_meta():
    """Minimal valid strategy metadata."""
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


# ---------------------------------------------------------------------------
# _check_entry_conditions
# ---------------------------------------------------------------------------

class TestCheckEntryConditions:

    def test_passes_in_normal_regime(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=48000,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is True
        assert "passed" in reason

    def test_passes_in_low_regime(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="low",
            atr_pct=Decimal("1.5"),
            current_price=Decimal("50000"),
            sma_50=48000,
            sma_200=45000,
            rsi_14=40,
        )
        assert ok is True

    def test_blocks_high_volatility_regime(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="high",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is False
        assert "volatility_regime=high" in reason

    def test_blocks_extreme_volatility_regime(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="extreme",
            atr_pct=Decimal("9.0"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is False
        assert "extreme" in reason

    def test_blocks_when_atr_pct_at_limit(self, strategy, base_meta):
        # Exactly at max_atr_pct_entry (6.0) should be blocked
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("6.0"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is False
        assert "atr_pct" in reason

    def test_blocks_when_atr_pct_exceeds_max(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("7.5"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is False
        assert "atr_pct" in reason

    def test_blocks_when_price_below_sma200(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("40000"),
            sma_50=None,
            sma_200=45000,      # price < sma_200
            rsi_14=45,
        )
        assert ok is False
        assert "sma_200" in reason

    def test_passes_when_sma200_is_none(self, strategy, base_meta):
        # sma_200 not available yet (insufficient history) — should not block
        ok, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=None,
            rsi_14=45,
        )
        assert ok is True

    def test_blocks_when_rsi_at_limit(self, strategy, base_meta):
        # Exactly at rsi_entry_max (60) should be blocked
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=48000,
            sma_200=45000,
            rsi_14=60,
        )
        assert ok is False
        assert "rsi_14" in reason

    def test_blocks_when_rsi_exceeds_max(self, strategy, base_meta):
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=48000,
            sma_200=45000,
            rsi_14=75,
        )
        assert ok is False
        assert "rsi_14" in reason

    def test_passes_when_rsi_is_none(self, strategy, base_meta):
        # rsi not available — should not block
        ok, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=48000,
            sma_200=45000,
            rsi_14=None,
        )
        assert ok is True

    def test_blocks_on_death_cross(self, strategy, base_meta):
        """SMA50 < SMA200 (death cross) should block entry even if price is above SMA200."""
        ok, reason = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=43000,       # below sma_200 — death cross
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is False
        assert "death cross" in reason

    def test_passes_when_sma50_above_sma200(self, strategy, base_meta):
        """SMA50 > SMA200 (golden cross) is healthy — should not block."""
        ok, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=47000,       # above sma_200 — golden cross
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is True

    def test_passes_when_sma50_is_none(self, strategy, base_meta):
        """SMA50 not yet available — death cross check is skipped."""
        ok, _ = strategy._check_entry_conditions(
            meta=base_meta,
            volatility_regime="normal",
            atr_pct=Decimal("3.0"),
            current_price=Decimal("50000"),
            sma_50=None,
            sma_200=45000,
            rsi_14=45,
        )
        assert ok is True


# ---------------------------------------------------------------------------
# _compute_grid_levels
# ---------------------------------------------------------------------------

class TestComputeGridLevels:

    def _make_levels(self, strategy, **overrides):
        defaults = dict(
            reference_price   = Decimal("100"),
            grid_spacing_pct  = Decimal("1.5"),
            num_levels        = 5,
            weights           = [1, 1, 2, 3, 3],
            capital_per_cycle = Decimal("1000"),
            atr_value         = Decimal("3"),
            atr_multiplier    = Decimal("0.5"),
        )
        return strategy._compute_grid_levels(**{**defaults, **overrides})

    def test_returns_correct_number_of_levels(self, strategy):
        levels = self._make_levels(strategy, num_levels=5)
        assert len(levels) == 5

    def test_level_nums_are_sequential(self, strategy):
        levels = self._make_levels(strategy)
        assert [l["level_num"] for l in levels] == [1, 2, 3, 4, 5]

    def test_prices_decrease_with_depth(self, strategy):
        levels = self._make_levels(strategy)
        prices = [l["target_price"] for l in levels]
        assert prices == sorted(prices, reverse=True)

    def test_level1_price_is_one_spacing_below_reference(self, strategy):
        # reference=100, spacing=1.5% → level1 = 100 * (1 - 0.015) = 98.5
        levels = self._make_levels(strategy, reference_price=Decimal("100"), grid_spacing_pct=Decimal("1.5"))
        expected = Decimal("100") * (1 - Decimal("1.5") / 100)
        assert levels[0]["target_price"] == expected

    def test_deepest_level_price_is_n_spacings_below_reference(self, strategy):
        levels = self._make_levels(strategy, reference_price=Decimal("100"), grid_spacing_pct=Decimal("2"), num_levels=5, weights=[1]*5)
        expected = Decimal("100") * (1 - 5 * Decimal("2") / 100)
        assert levels[-1]["target_price"] == expected

    def test_capital_allocation_sums_to_capital_per_cycle(self, strategy):
        levels = self._make_levels(strategy, capital_per_cycle=Decimal("1000"))
        total = sum(l["capital_allocated"] for l in levels)
        assert abs(total - Decimal("1000")) < Decimal("0.01")

    def test_heavier_weights_get_more_capital(self, strategy):
        # weights [1,1,2,3,3]: deeper levels should get more capital
        levels = self._make_levels(strategy, weights=[1, 1, 2, 3, 3])
        assert levels[4]["capital_allocated"] > levels[0]["capital_allocated"]
        assert levels[3]["capital_allocated"] > levels[1]["capital_allocated"]
        assert levels[2]["capital_allocated"] > levels[0]["capital_allocated"]

    def test_equal_weights_give_equal_capital(self, strategy):
        levels = self._make_levels(strategy, num_levels=4, weights=[1, 1, 1, 1], capital_per_cycle=Decimal("1000"))
        capitals = [l["capital_allocated"] for l in levels]
        assert all(abs(c - capitals[0]) < Decimal("0.001") for c in capitals)

    def test_quantity_equals_capital_over_price(self, strategy):
        levels = self._make_levels(strategy)
        for l in levels:
            expected_qty = l["capital_allocated"] / l["target_price"]
            assert abs(l["quantity"] - expected_qty) < Decimal("1e-12")

    def test_atr_metadata_stored_on_each_level(self, strategy):
        levels = self._make_levels(strategy, atr_value=Decimal("2500"), atr_multiplier=Decimal("0.5"))
        for l in levels:
            assert l["atr_value"] == Decimal("2500")
            assert l["atr_multiplier"] == Decimal("0.5")
            assert l["level_trigger"] == "initial"

    def test_single_level_gets_all_capital(self, strategy):
        levels = self._make_levels(strategy, num_levels=1, weights=[1], capital_per_cycle=Decimal("500"))
        assert len(levels) == 1
        assert abs(levels[0]["capital_allocated"] - Decimal("500")) < Decimal("0.01")


# ---------------------------------------------------------------------------
# _avg_entry
# ---------------------------------------------------------------------------

class TestAvgEntry:

    def _make_level(self, price, qty):
        return {"target_price": str(price), "quantity": str(qty)}

    def test_returns_zero_for_empty_list(self, strategy):
        assert strategy._avg_entry([]) == Decimal("0")

    def test_single_level(self, strategy):
        levels = [self._make_level(100, 1)]
        assert strategy._avg_entry(levels) == Decimal("100")

    def test_equal_quantities(self, strategy):
        levels = [
            self._make_level(99, 1),
            self._make_level(97, 1),
            self._make_level(95, 1),
        ]
        # avg = (99 + 97 + 95) / 3 = 97
        assert strategy._avg_entry(levels) == Decimal("97")

    def test_weighted_average(self, strategy):
        # Example from strategy doc: (99×1 + 97×2 + 95×3) / 6 = 96.333...
        levels = [
            self._make_level(99, 1),
            self._make_level(97, 2),
            self._make_level(95, 3),
        ]
        expected = (Decimal("99") * 1 + Decimal("97") * 2 + Decimal("95") * 3) / 6
        assert strategy._avg_entry(levels) == expected

    def test_larger_quantities_at_lower_prices_pull_average_down(self, strategy):
        equal_levels = [
            self._make_level(100, 1),
            self._make_level(90,  1),
        ]
        weighted_levels = [
            self._make_level(100, 1),
            self._make_level(90,  3),   # more weight at lower price
        ]
        avg_equal    = strategy._avg_entry(equal_levels)
        avg_weighted = strategy._avg_entry(weighted_levels)
        assert avg_weighted < avg_equal


# ---------------------------------------------------------------------------
# Shared helpers for fill-polling tests
# ---------------------------------------------------------------------------

def make_open_level(
    level_id=1, level_num=1, order_id=10,
    exchange_order_id="ex-order-001",
    trade_order_id=10,
    target_price="50000",
    quantity="0.02",
):
    """Return a dict that mimics an asyncpg Record for an OPEN grid level."""
    return {
        "id":                 level_id,
        "level_num":          level_num,
        "order_id":           order_id,
        "exchange_order_id":  exchange_order_id,
        "trade_order_id":     trade_order_id,
        "target_price":       target_price,
        "quantity":           quantity,
        "status":             "OPEN",
    }


def make_filled_order_status(
    exchange_order_id="ex-order-001",
    price=50000.0,
    amount=0.02,
    fee_cost=0.5,
    fee_currency="USDT",
):
    """Return a ccxt-shaped order dict representing a fully filled order."""
    return {
        "id":      exchange_order_id,
        "status":  "closed",
        "average": price,
        "price":   price,
        "filled":  amount,
        "remaining": 0.0,
        "cost":    price * amount,
        "fee":     {"cost": fee_cost, "currency": fee_currency},
        "trades":  [],
    }


def make_strategy(strategy_id=1):
    return {
        "id":               strategy_id,
        "venue_id":         1,
        "base_asset_id":    10,
        "quote_asset_id":   11,
        "base_asset_code":  "BTC",
        "quote_asset_code": "USDT",
        "taker_fee_pct":    "0.001",
        "metadata":         {},
    }


def make_cycle(cycle_id=100, strategy_id=1):
    return {
        "id":              cycle_id,
        "strategy_id":     strategy_id,
        "cycle_number":    1,
        "status":          "OPEN",
        "stop_loss_price": None,
        "profit_target_pct": "1.5",
        "grid_spacing_pct":  "1.5",
        "atr_at_open":       "3000",
    }


def make_mock_conn(open_levels=None):
    """Return an AsyncMock connection with fetch pre-configured."""
    conn = AsyncMock()
    conn.fetch       = AsyncMock(return_value=open_levels or [])
    conn.execute     = AsyncMock()
    conn.fetchval    = AsyncMock(return_value=None)
    # Make conn.transaction() work as an async context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


# ---------------------------------------------------------------------------
# _poll_open_orders
# ---------------------------------------------------------------------------

class TestPollOpenOrders:

    @pytest.mark.asyncio
    async def test_skips_when_no_open_levels(self, strategy):
        conn     = make_mock_conn(open_levels=[])
        exchange = AsyncMock()

        await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())

        exchange.fetch_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_fetch_order_for_each_open_level(self, strategy):
        levels = [
            make_open_level(level_id=1, exchange_order_id="ord-1"),
            make_open_level(level_id=2, exchange_order_id="ord-2"),
        ]
        conn     = make_mock_conn(open_levels=levels)
        exchange = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={"status": "open"})

        await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())

        assert exchange.fetch_order.await_count == 2
        exchange.fetch_order.assert_any_await("ord-1", "BTC/USDT")
        exchange.fetch_order.assert_any_await("ord-2", "BTC/USDT")

    @pytest.mark.asyncio
    async def test_records_fill_when_order_is_closed(self, strategy):
        level    = make_open_level(exchange_order_id="ord-1")
        conn     = make_mock_conn(open_levels=[level])
        exchange = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value=make_filled_order_status("ord-1"))

        with patch.object(strategy, "_record_fill", new=AsyncMock()) as mock_record:
            await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())
            mock_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_record_fill_when_order_still_open(self, strategy):
        level    = make_open_level(exchange_order_id="ord-1")
        conn     = make_mock_conn(open_levels=[level])
        exchange = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={"status": "open"})

        with patch.object(strategy, "_record_fill", new=AsyncMock()) as mock_record:
            await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())
            mock_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_after_fetch_order_failure(self, strategy):
        levels = [
            make_open_level(level_id=1, exchange_order_id="ord-1"),
            make_open_level(level_id=2, exchange_order_id="ord-2"),
        ]
        conn     = make_mock_conn(open_levels=levels)
        exchange = AsyncMock()
        # First call raises, second returns filled
        exchange.fetch_order = AsyncMock(
            side_effect=[Exception("timeout"), make_filled_order_status("ord-2")]
        )

        with patch.object(strategy, "_record_fill", new=AsyncMock()) as mock_record:
            # Should not raise despite the first failure
            await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())
            # Only the second (successful) order should trigger a fill
            mock_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_order_is_not_recorded_as_fill(self, strategy):
        level    = make_open_level()
        conn     = make_mock_conn(open_levels=[level])
        exchange = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={"status": "canceled"})

        with patch.object(strategy, "_record_fill", new=AsyncMock()) as mock_record:
            await strategy._poll_open_orders(exchange, conn, make_strategy(), make_cycle())
            mock_record.assert_not_awaited()


# ---------------------------------------------------------------------------
# _record_fill
# ---------------------------------------------------------------------------

class TestRecordFill:

    @pytest.mark.asyncio
    async def test_inserts_execution_record(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(trade_order_id=10, exchange_order_id="ord-1")
        order = make_filled_order_status("ord-1", price=49500.0, amount=0.02)

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        # First execute call should be the INSERT INTO trade_executions
        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "trade_executions" in first_call_sql

    @pytest.mark.asyncio
    async def test_updates_trade_order_to_filled(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(trade_order_id=10, exchange_order_id="ord-1")
        order = make_filled_order_status("ord-1")

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        second_call_sql = conn.execute.call_args_list[1][0][0]
        assert "trade_orders" in second_call_sql
        assert "FILLED" in second_call_sql

    @pytest.mark.asyncio
    async def test_updates_grid_level_to_filled(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(trade_order_id=10, exchange_order_id="ord-1")
        order = make_filled_order_status("ord-1")

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        third_call_sql = conn.execute.call_args_list[2][0][0]
        assert "trade_grid_levels" in third_call_sql
        assert "FILLED" in third_call_sql

    @pytest.mark.asyncio
    async def test_uses_order_average_price_for_execution(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(target_price="50000", exchange_order_id="ord-1")
        # Filled at 49800 (slippage from limit price)
        order = make_filled_order_status("ord-1", price=49800.0, amount=0.02)

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        exec_call_args = conn.execute.call_args_list[0][0]
        assert 49800.0 in exec_call_args  # executed_price

    @pytest.mark.asyncio
    async def test_falls_back_to_target_price_when_average_missing(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(target_price="48000", exchange_order_id="ord-1")
        order = {"id": "ord-1", "status": "closed", "average": None, "price": None,
                 "filled": 0.02, "fee": {}, "trades": []}

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        exec_call_args = conn.execute.call_args_list[0][0]
        assert 48000.0 in exec_call_args  # fell back to target_price

    @pytest.mark.asyncio
    async def test_uses_synthetic_execution_id_in_paper_mode(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(exchange_order_id="paper-uuid-123")
        order = make_filled_order_status("paper-uuid-123")   # trades=[]

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        exec_call_args = conn.execute.call_args_list[0][0]
        assert "paper-uuid-123_fill" in exec_call_args

    @pytest.mark.asyncio
    async def test_uses_trade_id_when_trades_present(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level(exchange_order_id="real-order-999")
        order = {
            "id": "real-order-999", "status": "closed",
            "average": 50000.0, "price": 50000.0, "filled": 0.01,
            "fee": {"cost": 0.5, "currency": "USDT"},
            "trades": [{"id": "trade-fill-42"}],
        }

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        exec_call_args = conn.execute.call_args_list[0][0]
        assert "trade-fill-42" in exec_call_args

    @pytest.mark.asyncio
    async def test_all_three_writes_use_same_transaction(self, strategy):
        conn  = make_mock_conn()
        level = make_open_level()
        order = make_filled_order_status()

        await strategy._record_fill(conn, make_strategy(), make_cycle(), level, order)

        # transaction() should have been entered exactly once
        conn.transaction.assert_called_once()
        # All three writes happened inside it
        assert conn.execute.await_count == 3


# ---------------------------------------------------------------------------
# Helpers for cycle-closer tests
# ---------------------------------------------------------------------------

def make_closing_cycle(cycle_id=200, strategy_id=1, cycle_number=2, close_trigger="take_profit"):
    return {
        "id":            cycle_id,
        "strategy_id":   strategy_id,
        "cycle_number":  cycle_number,
        "status":        "CLOSING",
        "close_trigger": close_trigger,
        "opened_at":     datetime.now(timezone.utc) - timedelta(hours=2),
    }


def make_sell_order(order_id=50, cycle_id=200, exchange_order_id="sell-ord-001", quantity="0.05"):
    return {
        "id":                 order_id,
        "cycle_id":           cycle_id,
        "exchange_order_id":  exchange_order_id,
        "side":               "SELL",
        "status":             "OPEN",
        "quantity":           quantity,
    }


def make_filled_sell_status(
    exchange_order_id="sell-ord-001",
    price=52000.0,
    amount=0.05,
    fee_cost=1.3,
    fee_currency="USDT",
):
    return {
        "id":      exchange_order_id,
        "status":  "closed",
        "average": price,
        "price":   price,
        "filled":  amount,
        "cost":    price * amount,
        "fee":     {"cost": fee_cost, "currency": fee_currency},
        "trades":  [],
    }


def make_buy_summary(total_qty="0.05", total_cost="2475.0", total_fees="0.5"):
    """Mimic an asyncpg fetchrow result for the buy-side aggregation query."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "total_qty":   total_qty,
        "total_cost":  total_cost,
        "total_fees":  total_fees,
    }[key]
    return row


def make_mock_conn_closing(closing_cycles=None, sell_order=None, buy_summary=None):
    """
    Build a mock connection for cycle-closer tests.

    fetch()    → closing_cycles list
    fetchrow() → sell_order on first call, buy_summary on second
    """
    conn = AsyncMock()
    conn.fetch    = AsyncMock(return_value=closing_cycles or [])
    # fetchrow called twice: once for sell_order, once for buy_summary
    conn.fetchrow = AsyncMock(side_effect=[sell_order, buy_summary])
    conn.execute  = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)

    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


# ---------------------------------------------------------------------------
# _poll_closing_cycles
# ---------------------------------------------------------------------------

class TestPollClosingCycles:

    @pytest.mark.asyncio
    async def test_skips_when_no_closing_cycles(self, strategy):
        conn     = make_mock_conn_closing(closing_cycles=[])
        exchange = AsyncMock()

        await strategy._poll_closing_cycles(exchange, conn, make_strategy())

        exchange.fetch_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_sell_order_status_from_exchange(self, strategy):
        cycle      = make_closing_cycle()
        sell_order = make_sell_order()
        buy_sum    = make_buy_summary()
        conn       = make_mock_conn_closing([cycle], sell_order, buy_sum)
        exchange   = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value=make_filled_sell_status())

        await strategy._poll_closing_cycles(exchange, conn, make_strategy())

        exchange.fetch_order.assert_awaited_once_with("sell-ord-001", "BTC/USDT")

    @pytest.mark.asyncio
    async def test_finalizes_when_sell_is_filled(self, strategy):
        cycle      = make_closing_cycle()
        sell_order = make_sell_order()
        buy_sum    = make_buy_summary()
        conn       = make_mock_conn_closing([cycle], sell_order, buy_sum)
        exchange   = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value=make_filled_sell_status())

        with patch.object(strategy, "_finalize_closed_cycle", new=AsyncMock()) as mock_fin:
            await strategy._poll_closing_cycles(exchange, conn, make_strategy())
            mock_fin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_finalize_when_sell_still_open(self, strategy):
        cycle      = make_closing_cycle()
        sell_order = make_sell_order()
        conn       = make_mock_conn_closing([cycle], sell_order, None)
        exchange   = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={"status": "open"})

        with patch.object(strategy, "_finalize_closed_cycle", new=AsyncMock()) as mock_fin:
            await strategy._poll_closing_cycles(exchange, conn, make_strategy())
            mock_fin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_warns_and_skips_when_no_sell_order_found(self, strategy):
        cycle = make_closing_cycle()
        conn  = make_mock_conn_closing([cycle], sell_order=None, buy_summary=None)
        # Reset fetchrow so it returns None (no sell order)
        conn.fetchrow = AsyncMock(return_value=None)
        exchange = AsyncMock()

        with patch.object(strategy, "_finalize_closed_cycle", new=AsyncMock()) as mock_fin:
            await strategy._poll_closing_cycles(exchange, conn, make_strategy())
            mock_fin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_after_fetch_order_exception(self, strategy):
        cycles = [make_closing_cycle(cycle_id=1), make_closing_cycle(cycle_id=2)]
        sell1  = make_sell_order(order_id=10, cycle_id=1, exchange_order_id="sell-1")
        sell2  = make_sell_order(order_id=11, cycle_id=2, exchange_order_id="sell-2")
        buy_sum = make_buy_summary()

        conn = AsyncMock()
        conn.fetch    = AsyncMock(return_value=cycles)
        conn.fetchrow = AsyncMock(side_effect=[sell1, sell2, buy_sum])
        conn.execute  = AsyncMock()
        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__  = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        exchange = AsyncMock()
        exchange.fetch_order = AsyncMock(
            side_effect=[Exception("timeout"), make_filled_sell_status("sell-2")]
        )

        with patch.object(strategy, "_finalize_closed_cycle", new=AsyncMock()) as mock_fin:
            await strategy._poll_closing_cycles(exchange, conn, make_strategy())
            # Only the second cycle (successful fetch) should be finalized
            mock_fin.assert_awaited_once()


# ---------------------------------------------------------------------------
# _finalize_closed_cycle
# ---------------------------------------------------------------------------

class TestFinalizeClosedCycle:

    def _make_inputs(self, close_trigger="take_profit"):
        cycle      = make_closing_cycle(close_trigger=close_trigger)
        sell_order = make_sell_order()
        order_status = make_filled_sell_status(price=52000.0, amount=0.05, fee_cost=1.3)
        buy_sum    = make_buy_summary(total_qty="0.05", total_cost="2475.0", total_fees="0.5")
        conn       = make_mock_conn_closing([cycle], sell_order, buy_sum)
        # fetchrow is called once inside _finalize_closed_cycle (buy summary)
        conn.fetchrow = AsyncMock(return_value=buy_sum)
        return conn, cycle, sell_order, order_status

    @pytest.mark.asyncio
    async def test_writes_exactly_seven_records(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        # 7 execute calls: executions, orders, grid_levels, pnl, locks, cycles, events
        assert conn.execute.await_count == 7

    @pytest.mark.asyncio
    async def test_inserts_sell_execution(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        first_sql = conn.execute.call_args_list[0][0][0]
        assert "trade_executions" in first_sql
        assert "SELL" in first_sql

    @pytest.mark.asyncio
    async def test_marks_sell_order_filled(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        second_sql = conn.execute.call_args_list[1][0][0]
        assert "trade_orders" in second_sql
        assert "FILLED" in second_sql

    @pytest.mark.asyncio
    async def test_cancels_remaining_grid_levels(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        third_sql = conn.execute.call_args_list[2][0][0]
        assert "trade_grid_levels" in third_sql
        assert "CANCELLED" in third_sql

    @pytest.mark.asyncio
    async def test_inserts_trade_pnl(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        fourth_sql = conn.execute.call_args_list[3][0][0]
        assert "trade_pnl" in fourth_sql

    @pytest.mark.asyncio
    async def test_releases_capital_lock(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        fifth_sql = conn.execute.call_args_list[4][0][0]
        assert "capital_locks" in fifth_sql
        assert "RELEASED" in fifth_sql

    @pytest.mark.asyncio
    async def test_marks_cycle_closed(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        sixth_sql = conn.execute.call_args_list[5][0][0]
        assert "trade_cycles" in sixth_sql
        assert "CLOSED" in sixth_sql

    @pytest.mark.asyncio
    async def test_logs_system_event(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        seventh_sql = conn.execute.call_args_list[6][0][0]
        assert "system_events" in seventh_sql
        assert "CYCLE_CLOSED" in seventh_sql

    @pytest.mark.asyncio
    async def test_pnl_maths_are_correct(self, strategy):
        # buy_cost=2475, sell_proceeds=52000*0.05=2600, buy_fees=0.5, sell_fee=1.3
        # gross = 2600 - 2475 = 125
        # total_fees = 0.5 + 1.3 = 1.8
        # net = 125 - 1.8 = 123.2
        # pnl_pct = 123.2 / 2475 * 100 ≈ 4.979...
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        pnl_call_args = conn.execute.call_args_list[3][0]
        # args: cycle_id, strategy_id, buy_qty, buy_cost, avg_buy,
        #       sell_qty, sell_proceeds, avg_sell,
        #       total_fees, gross_pnl, net_pnl, pnl_pct, duration
        gross_pnl  = pnl_call_args[10]
        net_pnl    = pnl_call_args[11]
        total_fees = pnl_call_args[9]
        assert abs(gross_pnl - 125.0)  < 0.01
        assert abs(total_fees - 1.8)   < 0.01
        assert abs(net_pnl - 123.2)    < 0.01

    @pytest.mark.asyncio
    async def test_all_writes_in_single_transaction(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        conn.transaction.assert_called_once()
        assert conn.execute.await_count == 7

    @pytest.mark.asyncio
    async def test_uses_synthetic_execution_id_for_paper_sell(self, strategy):
        conn, cycle, sell_order, order_status = self._make_inputs()
        # trades=[] means paper mode
        await strategy._finalize_closed_cycle(
            conn, make_strategy(), cycle, sell_order, order_status,
        )
        exec_args = conn.execute.call_args_list[0][0]
        assert "sell-ord-001_fill" in exec_args


# ---------------------------------------------------------------------------
# Shared helpers for _maybe_retune tests
# ---------------------------------------------------------------------------

def make_cycle_with_regime(cycle_id=100, regime="normal"):
    return {
        "id":                cycle_id,
        "strategy_id":       1,
        "cycle_number":      2,
        "status":            "OPEN",
        "stop_loss_price":   "47000",
        "profit_target_pct": "1.5",
        "grid_spacing_pct":  "1.5",
        "atr_at_open":       "3000",
        "volatility_regime": regime,
        "current_multiplier": "0.5",
    }


def make_indicators_retune(regime="low", atr_14=2500.0, atr_pct=1.5):
    return {
        "volatility_regime": regime,
        "atr_14":    atr_14,
        "atr_pct":   atr_pct,
        "atr_sma_20": None,
        "sma_50":    None,
        "sma_200":   None,
        "rsi_14":    None,
        "metric_date": None,
    }


def make_pending_level_retune(
    level_id=1,
    level_num=1,
    target_price="49000",
    capital_allocated="500",
    quantity="0.0102",
):
    return {
        "id":                level_id,
        "level_num":         level_num,
        "target_price":      target_price,
        "capital_allocated": capital_allocated,
        "quantity":          quantity,
        "status":            "PENDING",
    }


def make_strategy_with_meta():
    return {
        "id":               1,
        "venue_id":         1,
        "base_asset_id":    10,
        "quote_asset_id":   11,
        "base_asset_code":  "BTC",
        "quote_asset_code": "USDT",
        "taker_fee_pct":    "0.001",
        "metadata": {
            "capital_per_cycle":     1000,
            "num_levels":            3,
            "weights":               [1, 2, 3],
            "atr_multiplier_low":    0.4,
            "atr_multiplier_normal": 0.5,
            "atr_multiplier_high":   0.7,
            "profit_target_low":     1.0,
            "profit_target_normal":  1.5,
            "profit_target_high":    2.5,
            "max_atr_pct_entry":     6.0,
            "rsi_entry_max":         60,
            "reserve_capital_pct":   30,
        },
    }


def make_conn_retune(indicators=None):
    conn = AsyncMock()
    conn.fetchrow    = AsyncMock(return_value=indicators)
    conn.execute     = AsyncMock()
    conn.executemany = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


# ---------------------------------------------------------------------------
# _maybe_retune
# ---------------------------------------------------------------------------

class TestMaybeRetune:

    @pytest.mark.asyncio
    async def test_returns_none_when_indicators_unavailable(self, strategy):
        conn   = make_conn_retune(indicators=None)
        cycle  = make_cycle_with_regime(regime="normal")
        result = await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_regime_unchanged(self, strategy):
        conn   = make_conn_retune(indicators=make_indicators_retune(regime="normal"))
        cycle  = make_cycle_with_regime(regime="normal")
        result = await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        assert result is None
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_retune_into_high_regime(self, strategy):
        conn   = make_conn_retune(indicators=make_indicators_retune(regime="high"))
        cycle  = make_cycle_with_regime(regime="normal")
        result = await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        assert result is None
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_retune_into_extreme_regime(self, strategy):
        conn   = make_conn_retune(indicators=make_indicators_retune(regime="extreme"))
        cycle  = make_cycle_with_regime(regime="normal")
        result = await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        assert result is None
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_new_profit_target_when_retuned(self, strategy):
        # normal → low: profit_target_low = 1.0 from make_strategy_with_meta
        conn   = make_conn_retune(indicators=make_indicators_retune(regime="low"))
        cycle  = make_cycle_with_regime(regime="normal")
        result = await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        assert result == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_updates_dca_cycle_details(self, strategy):
        conn  = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=1.5))
        cycle = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("trade_dca_cycle_details" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_logs_system_event(self, strategy):
        conn  = make_conn_retune(indicators=make_indicators_retune(regime="low"))
        cycle = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("GRID_RETUNED" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_reprices_pending_levels(self, strategy):
        pending = [make_pending_level_retune(level_id=1)]
        conn    = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=1.5))
        cycle   = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), pending,
        )
        conn.executemany.assert_awaited_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_does_not_reprice_open_or_filled_levels(self, strategy):
        open_lvl   = {**make_pending_level_retune(level_id=2), "status": "OPEN"}
        filled_lvl = {**make_pending_level_retune(level_id=3), "status": "FILLED"}
        conn  = make_conn_retune(indicators=make_indicators_retune(regime="low"))
        cycle = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"),
            [open_lvl, filled_lvl],
        )
        conn.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_stop_loss_when_pending_exist(self, strategy):
        pending = [make_pending_level_retune(level_id=1)]
        conn    = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=1.5))
        cycle   = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), pending,
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("trade_cycles" in s and "stop_loss_price" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_no_stop_loss_update_when_no_pending_levels(self, strategy):
        conn  = make_conn_retune(indicators=make_indicators_retune(regime="low"))
        cycle = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), [],
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert not any("trade_cycles" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_all_writes_in_single_transaction(self, strategy):
        pending = [make_pending_level_retune(level_id=1)]
        conn    = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=1.5))
        cycle   = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), pending,
        )
        conn.transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_level_repriced_below_current_price(self, strategy):
        """Repriced target must be strictly below the anchor (current price when no open levels)."""
        pending = [make_pending_level_retune(level_id=1, capital_allocated="500")]
        conn    = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=2.0))
        cycle   = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"), pending,
        )
        row = conn.executemany.call_args[0][1][0]
        repriced_target = row[0]   # first element in tuple: new_target
        assert repriced_target < 50000.0

    @pytest.mark.asyncio
    async def test_uses_lowest_open_level_as_anchor(self, strategy):
        """When an OPEN level exists, pending levels anchor off the lowest open price."""
        open_lvl = {**make_pending_level_retune(level_id=2, target_price="48000"), "status": "OPEN"}
        pending  = [make_pending_level_retune(level_id=3, level_num=2, capital_allocated="500")]
        conn     = make_conn_retune(indicators=make_indicators_retune(regime="low", atr_pct=2.0))
        cycle    = make_cycle_with_regime(regime="normal")
        await strategy._maybe_retune(
            conn, make_strategy_with_meta(), cycle, Decimal("50000"),
            [open_lvl, pending[0]],
        )
        row = conn.executemany.call_args[0][1][0]
        repriced_target = row[0]
        # anchor=48000, so repriced target must be below 48000, not just below 50000
        assert repriced_target < 48000.0


# ---------------------------------------------------------------------------
# Shared helpers for crash-protection tests
# ---------------------------------------------------------------------------

def make_indicators_cb(regime="extreme", atr_pct=9.0, atr_14=4500.0):
    return {
        "volatility_regime": regime,
        "atr_14":    atr_14,
        "atr_pct":   atr_pct,
        "atr_sma_20": None,
        "sma_50":    None,
        "sma_200":   None,
        "rsi_14":    None,
        "metric_date": None,
    }


def make_filled_level_cp(level_id, target_price="49000", quantity="0.02"):
    return {
        "id":            level_id,
        "level_num":     level_id,
        "target_price":  target_price,
        "quantity":      quantity,
        "status":        "FILLED",
        "level_trigger": "initial",
    }


def make_cycle_cp(cycle_id=100, stop_loss_price="46000", capital_allocated="1000"):
    return {
        "id":                cycle_id,
        "strategy_id":       1,
        "cycle_number":      1,
        "status":            "OPEN",
        "stop_loss_price":   stop_loss_price,
        "capital_allocated": capital_allocated,
        "profit_target_pct": "1.5",
        "volatility_regime": "normal",
    }


def make_conn_cb(indicators=None):
    conn = AsyncMock()
    conn.fetchrow    = AsyncMock(return_value=indicators)
    conn.execute     = AsyncMock()
    conn.executemany = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


# ---------------------------------------------------------------------------
# _check_circuit_breaker
# ---------------------------------------------------------------------------

class TestCheckCircuitBreaker:

    @pytest.mark.asyncio
    async def test_returns_false_when_regime_is_normal(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle = make_cycle_cp()
        result = await strategy._check_circuit_breaker(
            AsyncMock(), conn, make_strategy(), cycle, [], Decimal("50000"),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_indicators_unavailable(self, strategy):
        conn  = make_conn_cb(indicators=None)
        cycle = make_cycle_cp()
        result = await strategy._check_circuit_breaker(
            AsyncMock(), conn, make_strategy(), cycle, [], Decimal("50000"),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_regime_is_extreme(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="extreme", atr_pct=9.0))
        cycle = make_cycle_cp()
        filled = [make_filled_level_cp(1)]
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={"id": "sell-999"})
        result = await strategy._check_circuit_breaker(
            exchange, conn, make_strategy(), cycle, filled, Decimal("50000"),
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_atr_exceeds_threshold(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="high", atr_pct=9.0))
        cycle = make_cycle_cp()
        filled = [make_filled_level_cp(1)]
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={"id": "sell-999"})
        # default cb threshold is 8.0; atr_pct=9.0 should trigger
        result = await strategy._check_circuit_breaker(
            exchange, conn, make_strategy(), cycle, filled, Decimal("50000"),
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_does_not_close_when_no_filled_levels(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="extreme"))
        cycle = make_cycle_cp()
        exchange = AsyncMock()
        with patch.object(strategy, "_close_cycle", new=AsyncMock()) as mock_close:
            await strategy._check_circuit_breaker(
                exchange, conn, make_strategy(), cycle, [], Decimal("50000"),
            )
            mock_close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_triggers_close_with_correct_trigger_label(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="extreme"))
        cycle = make_cycle_cp()
        filled = [make_filled_level_cp(1)]
        exchange = AsyncMock()
        with patch.object(strategy, "_close_cycle", new=AsyncMock()) as mock_close:
            await strategy._check_circuit_breaker(
                exchange, conn, make_strategy(), cycle, filled, Decimal("50000"),
            )
            mock_close.assert_awaited_once()
            _, kwargs = mock_close.call_args[0], mock_close.call_args[1]
            trigger = mock_close.call_args[1].get("trigger") or mock_close.call_args[0][-1]
            assert trigger == "circuit_breaker"

    @pytest.mark.asyncio
    async def test_logs_system_event_on_trigger(self, strategy):
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="extreme"))
        cycle = make_cycle_cp()
        exchange = AsyncMock()
        with patch.object(strategy, "_close_cycle", new=AsyncMock()):
            await strategy._check_circuit_breaker(
                exchange, conn, make_strategy(), cycle, [make_filled_level_cp(1)], Decimal("50000"),
            )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("CIRCUIT_BREAKER_TRIGGERED" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_respects_custom_threshold_from_meta(self, strategy):
        """circuit_breaker_atr_pct in metadata overrides the default 8.0."""
        conn  = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=5.5))
        cycle = make_cycle_cp()
        strat = {**make_strategy_with_meta()}
        strat["metadata"] = {**strat["metadata"], "circuit_breaker_atr_pct": 5.0}
        exchange = AsyncMock()
        with patch.object(strategy, "_close_cycle", new=AsyncMock()):
            result = await strategy._check_circuit_breaker(
                exchange, conn, strat, cycle, [make_filled_level_cp(1)], Decimal("50000"),
            )
        assert result is True


# ---------------------------------------------------------------------------
# _maybe_expand_grid
# ---------------------------------------------------------------------------

class TestMaybeExpandGrid:

    def _levels(self, filled=2, open_=0, pending=0, crash=0):
        """Build a mixed level list for testing."""
        levels = []
        num = 1
        for _ in range(filled):
            levels.append({
                "id": num, "level_num": num,
                "target_price": str(50000 - num * 500),
                "quantity": "0.02", "capital_allocated": "500",
                "status": "FILLED", "level_trigger": "initial",
            })
            num += 1
        for _ in range(open_):
            levels.append({
                "id": num, "level_num": num,
                "target_price": str(50000 - num * 500),
                "quantity": "0.02", "capital_allocated": "500",
                "status": "OPEN", "level_trigger": "initial",
            })
            num += 1
        for _ in range(pending):
            levels.append({
                "id": num, "level_num": num,
                "target_price": str(50000 - num * 500),
                "quantity": "0.02", "capital_allocated": "500",
                "status": "PENDING", "level_trigger": "initial",
            })
            num += 1
        for _ in range(crash):
            levels.append({
                "id": num, "level_num": num,
                "target_price": str(50000 - num * 500),
                "quantity": "0.01", "capital_allocated": "250",
                "status": "PENDING", "level_trigger": "crash_expansion",
            })
            num += 1
        return levels

    @pytest.mark.asyncio
    async def test_returns_false_when_pending_levels_remain(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=1, pending=1)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("48000"), levels,
        )
        assert result is False
        conn.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_filled_levels(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=0, pending=0)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("48000"), levels,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_price_at_or_below_stop_loss(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp(stop_loss_price="48000")
        levels = self._levels(filled=2)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("48000"), levels,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_indicators_unavailable(self, strategy):
        conn   = make_conn_cb(indicators=None)
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_max_expansions_reached(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        # expansion_levels=2 per meta default; 1 expansion already done → 2 crash levels
        levels = self._levels(filled=2, crash=2)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_adds_expansion_levels_when_conditions_met(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        result = await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        assert result is True
        conn.executemany.assert_awaited_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 2   # expansion_levels default = 2

    @pytest.mark.asyncio
    async def test_new_levels_use_crash_expansion_trigger(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        rows = conn.executemany.call_args[0][1]
        for row in rows:
            assert row[9] == "crash_expansion"   # 10th element = level_trigger

    @pytest.mark.asyncio
    async def test_logs_grid_expanded_event(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("GRID_EXPANDED" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_updates_stop_loss_price(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("trade_cycles" in s and "stop_loss_price" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_all_writes_in_single_transaction(self, strategy):
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        conn.transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_expansion_levels_are_deeper_than_existing(self, strategy):
        """New crash levels must be priced below the deepest existing level."""
        conn   = make_conn_cb(indicators=make_indicators_cb(regime="normal", atr_pct=3.0))
        cycle  = make_cycle_cp()
        levels = self._levels(filled=2)
        # Deepest level price = 50000 - 2*500 = 49000
        deepest_existing = min(float(l["target_price"]) for l in levels)
        await strategy._maybe_expand_grid(
            conn, make_strategy_with_meta(), cycle, Decimal("49000"), levels,
        )
        rows = conn.executemany.call_args[0][1]
        for row in rows:
            assert row[3] < deepest_existing   # 4th element = target_price
