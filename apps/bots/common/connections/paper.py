"""
Paper trading connection — wraps a real exchange connection for market data
but returns simulated responses for all order operations.

Use this during development and testing to run the full bot loop without
submitting real orders to the exchange.

Market data (fetch_ticker, fetch_tickers, fetch_ohlcv, fetch_orderbook) is
delegated to the underlying real exchange unchanged — prices are live.

Order operations (create_order, cancel_order, fetch_order, fetch_open_orders,
fetch_balance) return synthetic responses that mimic the ccxt order dict shape.

Fill simulation strategy:
  - create_order  → status='open' (order accepted, not yet filled)
  - fetch_order   → status='closed' (instant fill on first status check)
  - cancel_order  → status='canceled'
  - fetch_balance → configurable fake quote balance
"""

import logging
import uuid
from datetime import datetime, timezone

from .base import BaseExchangeConnection, Candle, OrderBook, Ticker, TradingFees

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class PaperTradingConnection(BaseExchangeConnection):
    """
    Wraps any BaseExchangeConnection for paper (simulated) trading.

    Args:
        real_exchange:   Underlying connection used for all market data calls.
        initial_balance: Starting quote balance for the simulated account (USDT).
    """

    def __init__(
        self,
        real_exchange: BaseExchangeConnection,
        initial_balance: float = 10_000.0,
    ) -> None:
        self._exchange = real_exchange
        self._balance = initial_balance
        # In-memory order store: order_id → order dict
        self._orders: dict[str, dict] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._exchange.close()

    # ── Public market data — delegated to real exchange ───────────────────────

    async def fetch_trading_fees(self, symbol: str) -> TradingFees:
        return await self._exchange.fetch_trading_fees(symbol)

    async def fetch_ticker(self, symbol: str) -> Ticker:
        return await self._exchange.fetch_ticker(symbol)

    async def fetch_tickers(self, symbols: list[str]) -> dict[str, Ticker]:
        return await self._exchange.fetch_tickers(symbols)

    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        return await self._exchange.fetch_orderbook(symbol, limit=limit)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> list[Candle]:
        return await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    # ── Private trading — simulated ───────────────────────────────────────────

    async def fetch_balance(self) -> dict:
        """Return a synthetic balance dict shaped like ccxt fetch_balance output."""
        return {
            "USDT":  {"free": self._balance, "used": 0.0, "total": self._balance},
            "free":  {"USDT": self._balance},
            "used":  {"USDT": 0.0},
            "total": {"USDT": self._balance},
        }

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
    ) -> dict:
        """
        Simulate order submission.

        Returns a ccxt-shaped order dict with status='open'.
        The order is stored in memory so subsequent fetch_order calls can
        return a consistent filled response.
        """
        order_id = str(uuid.uuid4())
        ts = _now_ms()

        order: dict = {
            "id":                 order_id,
            "clientOrderId":      order_id,
            "datetime":           _iso(ts),
            "timestamp":          ts,
            "lastTradeTimestamp": None,
            "status":             "open",
            "symbol":             symbol,
            "type":               order_type,
            "side":               side,
            "price":              price,
            "average":            None,
            "amount":             amount,
            "filled":             0.0,
            "remaining":          amount,
            "cost":               0.0,
            "fee":                {"currency": "USDT", "cost": 0.0, "rate": 0.0},
            "trades":             [],
            "info":               {"paper": True},
        }

        self._orders[order_id] = order

        logger.info(
            "[PAPER] create_order | %s %s %s | qty=%.8f price=%s | id=%s",
            symbol, side.upper(), order_type.upper(), amount, price, order_id,
        )
        return order

    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        """
        Simulate order status check.

        On first call after create_order the order is returned as fully filled
        (status='closed'), modelling instant execution. Subsequent calls return
        the same filled state.
        """
        order = self._orders.get(order_id)

        if order is None:
            # Unknown order — return a safe closed response so the bot can continue.
            logger.warning("[PAPER] fetch_order: unknown order_id=%s, returning closed.", order_id)
            return {
                "id": order_id, "status": "closed",
                "filled": 0.0, "remaining": 0.0, "info": {"paper": True},
            }

        if order["status"] == "open":
            fill_ts = _now_ms()
            order = {
                **order,
                "status":             "closed",
                "average":            order["price"],
                "filled":             order["amount"],
                "remaining":          0.0,
                "cost":               (order["price"] or 0.0) * order["amount"],
                "lastTradeTimestamp": fill_ts,
            }
            self._orders[order_id] = order

            logger.info(
                "[PAPER] fetch_order | %s %s | FILLED qty=%.8f @ %s | id=%s",
                order["symbol"], order["side"].upper(),
                order["amount"], order["price"], order_id,
            )

        return order

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Simulate order cancellation."""
        order = self._orders.get(order_id, {"id": order_id, "amount": 0.0})
        cancelled = {
            **order,
            "status":    "canceled",
            "filled":    order.get("filled", 0.0),
            "remaining": order.get("remaining", order.get("amount", 0.0)),
        }
        self._orders[order_id] = cancelled
        logger.info("[PAPER] cancel_order | id=%s", order_id)
        return cancelled

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Return all in-memory open orders, optionally filtered by symbol."""
        open_orders = [o for o in self._orders.values() if o["status"] == "open"]
        if symbol:
            open_orders = [o for o in open_orders if o["symbol"] == symbol]
        return open_orders
