"""
Unit tests for PaperTradingConnection.

Validates that the paper exchange:
  - Delegates all market data calls to the underlying real exchange
  - Simulates the full order lifecycle (create → open → fetch → filled)
  - Handles cancellation correctly
  - Returns a realistic balance dict
  - Never raises on unknown order IDs

No real exchange connection is used — the underlying exchange is replaced
with a lightweight AsyncMock.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from common.connections.paper import PaperTradingConnection
from common.connections.base import Ticker, OrderBook, Candle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ticker(symbol: str, last: float = 50000.0) -> Ticker:
    return Ticker(
        symbol=symbol, last=last, bid=last - 10, ask=last + 10,
        spread_pct=0.04, volume_24h=1_000_000.0,
        timestamp=datetime.now(timezone.utc),
    )


def make_candle(close: float = 50000.0) -> Candle:
    return Candle(
        timestamp=datetime.now(timezone.utc),
        open=close - 100, high=close + 200, low=close - 200, close=close, volume=10.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_real_exchange():
    """A mock underlying exchange that returns canned market data."""
    m = AsyncMock()
    m.fetch_ticker    = AsyncMock(return_value=make_ticker("BTC/USDT"))
    m.fetch_tickers   = AsyncMock(return_value={"BTC/USDT": make_ticker("BTC/USDT")})
    m.fetch_orderbook = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT", bids=[(49990.0, 1.0)], asks=[(50010.0, 1.0)],
        timestamp=datetime.now(timezone.utc),
    ))
    m.fetch_ohlcv     = AsyncMock(return_value=[make_candle()])
    m.close           = AsyncMock()
    return m


@pytest.fixture
def paper(mock_real_exchange):
    return PaperTradingConnection(mock_real_exchange, initial_balance=10_000.0)


# ---------------------------------------------------------------------------
# Market data delegation
# ---------------------------------------------------------------------------

class TestMarketDataDelegation:

    @pytest.mark.asyncio
    async def test_fetch_ticker_delegates_to_real_exchange(self, paper, mock_real_exchange):
        result = await paper.fetch_ticker("BTC/USDT")
        mock_real_exchange.fetch_ticker.assert_awaited_once_with("BTC/USDT")
        assert result["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_fetch_tickers_delegates_to_real_exchange(self, paper, mock_real_exchange):
        result = await paper.fetch_tickers(["BTC/USDT"])
        mock_real_exchange.fetch_tickers.assert_awaited_once_with(["BTC/USDT"])
        assert "BTC/USDT" in result

    @pytest.mark.asyncio
    async def test_fetch_orderbook_delegates_to_real_exchange(self, paper, mock_real_exchange):
        result = await paper.fetch_orderbook("BTC/USDT", limit=10)
        mock_real_exchange.fetch_orderbook.assert_awaited_once_with("BTC/USDT", limit=10)
        assert result["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_delegates_to_real_exchange(self, paper, mock_real_exchange):
        result = await paper.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=50)
        mock_real_exchange.fetch_ohlcv.assert_awaited_once_with("BTC/USDT", timeframe="1h", limit=50)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_close_delegates_to_real_exchange(self, paper, mock_real_exchange):
        await paper.close()
        mock_real_exchange.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# fetch_balance
# ---------------------------------------------------------------------------

class TestFetchBalance:

    @pytest.mark.asyncio
    async def test_returns_configured_initial_balance(self, paper):
        balance = await paper.fetch_balance()
        assert balance["USDT"]["free"]  == 10_000.0
        assert balance["USDT"]["total"] == 10_000.0
        assert balance["USDT"]["used"]  == 0.0

    @pytest.mark.asyncio
    async def test_custom_initial_balance(self, mock_real_exchange):
        paper = PaperTradingConnection(mock_real_exchange, initial_balance=5_000.0)
        balance = await paper.fetch_balance()
        assert balance["USDT"]["free"] == 5_000.0
        assert balance["free"]["USDT"] == 5_000.0


# ---------------------------------------------------------------------------
# create_order
# ---------------------------------------------------------------------------

class TestCreateOrder:

    @pytest.mark.asyncio
    async def test_returns_open_status(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        assert order["status"] == "open"

    @pytest.mark.asyncio
    async def test_order_has_required_fields(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        for field in ("id", "symbol", "side", "type", "amount", "price", "status", "filled", "remaining"):
            assert field in order, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_order_id_is_unique(self, paper):
        o1 = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        o2 = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=49000.0)
        assert o1["id"] != o2["id"]

    @pytest.mark.asyncio
    async def test_filled_is_zero_on_creation(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        assert order["filled"] == 0.0
        assert order["remaining"] == 0.01

    @pytest.mark.asyncio
    async def test_market_order_no_price(self, paper):
        order = await paper.create_order("BTC/USDT", "sell", "market", 0.01)
        assert order["status"] == "open"
        assert order["price"] is None

    @pytest.mark.asyncio
    async def test_order_stored_in_memory(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        assert order["id"] in paper._orders

    @pytest.mark.asyncio
    async def test_info_marks_order_as_paper(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        assert order["info"]["paper"] is True


# ---------------------------------------------------------------------------
# fetch_order — fill simulation
# ---------------------------------------------------------------------------

class TestFetchOrder:

    @pytest.mark.asyncio
    async def test_first_fetch_returns_filled(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        filled = await paper.fetch_order(order["id"], "BTC/USDT")
        assert filled["status"] == "closed"

    @pytest.mark.asyncio
    async def test_filled_quantity_equals_original_amount(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.02, price=50000.0)
        filled = await paper.fetch_order(order["id"], "BTC/USDT")
        assert filled["filled"]    == 0.02
        assert filled["remaining"] == 0.0

    @pytest.mark.asyncio
    async def test_average_fill_price_equals_limit_price(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=49500.0)
        filled = await paper.fetch_order(order["id"], "BTC/USDT")
        assert filled["average"] == 49500.0

    @pytest.mark.asyncio
    async def test_cost_is_price_times_quantity(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.02, price=50000.0)
        filled = await paper.fetch_order(order["id"], "BTC/USDT")
        assert filled["cost"] == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_subsequent_fetch_returns_same_filled_state(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        filled1 = await paper.fetch_order(order["id"], "BTC/USDT")
        filled2 = await paper.fetch_order(order["id"], "BTC/USDT")
        assert filled1["status"] == filled2["status"] == "closed"
        assert filled1["filled"] == filled2["filled"]

    @pytest.mark.asyncio
    async def test_unknown_order_id_returns_closed_safely(self, paper):
        result = await paper.fetch_order("nonexistent-id", "BTC/USDT")
        assert result["status"] == "closed"
        assert result["filled"] == 0.0


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

class TestCancelOrder:

    @pytest.mark.asyncio
    async def test_cancel_returns_canceled_status(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        cancelled = await paper.cancel_order(order["id"], "BTC/USDT")
        assert cancelled["status"] == "canceled"

    @pytest.mark.asyncio
    async def test_cancelled_order_not_in_open_orders(self, paper):
        order = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        await paper.cancel_order(order["id"], "BTC/USDT")
        open_orders = await paper.fetch_open_orders("BTC/USDT")
        assert not any(o["id"] == order["id"] for o in open_orders)

    @pytest.mark.asyncio
    async def test_cancel_unknown_order_does_not_raise(self, paper):
        result = await paper.cancel_order("ghost-id", "BTC/USDT")
        assert result["status"] == "canceled"


# ---------------------------------------------------------------------------
# fetch_open_orders
# ---------------------------------------------------------------------------

class TestFetchOpenOrders:

    @pytest.mark.asyncio
    async def test_returns_only_open_orders(self, paper):
        o1 = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        o2 = await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=49000.0)
        # Fill o1
        await paper.fetch_order(o1["id"], "BTC/USDT")

        open_orders = await paper.fetch_open_orders("BTC/USDT")
        ids = [o["id"] for o in open_orders]
        assert o1["id"] not in ids
        assert o2["id"] in ids

    @pytest.mark.asyncio
    async def test_symbol_filter_applied(self, paper):
        await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        await paper.create_order("ETH/USDT", "buy", "limit", 0.1,  price=3000.0)

        btc_orders = await paper.fetch_open_orders("BTC/USDT")
        assert all(o["symbol"] == "BTC/USDT" for o in btc_orders)

    @pytest.mark.asyncio
    async def test_no_symbol_filter_returns_all_open(self, paper):
        await paper.create_order("BTC/USDT", "buy", "limit", 0.01, price=50000.0)
        await paper.create_order("ETH/USDT", "buy", "limit", 0.1,  price=3000.0)

        all_open = await paper.fetch_open_orders()
        assert len(all_open) == 2

    @pytest.mark.asyncio
    async def test_empty_when_no_orders_placed(self, paper):
        open_orders = await paper.fetch_open_orders()
        assert open_orders == []
