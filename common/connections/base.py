"""
Abstract base class for exchange connections.

All exchange implementations (REST today, WebSocket in future) must implement
this interface. Bots only interact with this contract — they never import ccxt
or exchange-specific code directly.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TypedDict


# ── Normalised data types ──────────────────────────────────────────────────────

class Ticker(TypedDict):
    symbol:      str
    last:        float | None
    bid:         float | None
    ask:         float | None
    spread_pct:  float | None   # (ask - bid) / bid * 100
    volume_24h:  float | None   # quote volume (e.g. USDT)
    timestamp:   datetime


class OrderBook(TypedDict):
    symbol:    str
    bids:      list[tuple[float, float]]   # [[price, amount], ...]
    asks:      list[tuple[float, float]]
    timestamp: datetime


class Candle(TypedDict):
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float            # base volume


class TradingFees(TypedDict):
    symbol: str
    maker:  float   # e.g. 0.0025 = 0.25%
    taker:  float   # e.g. 0.005  = 0.50%


# ── Abstract interface ─────────────────────────────────────────────────────────

class BaseExchangeConnection(ABC):
    """
    Common interface for all exchange connections.

    Public (no auth required):
        fetch_ticker, fetch_tickers, fetch_orderbook, fetch_ohlcv

    Private (auth required — implemented in subclass when credentials provided):
        fetch_balance, create_order, cancel_order, fetch_order, fetch_open_orders
    """

    # -- Lifecycle --

    @abstractmethod
    async def close(self) -> None:
        """Release underlying connections / sessions."""

    # -- Public market data --

    @abstractmethod
    async def fetch_trading_fees(self, symbol: str) -> TradingFees:
        """
        Return maker and taker fee rates for a symbol.
        Fetched from the exchange's market data (requires load_markets).
        e.g. TradingFees(symbol='BTC/USDT', maker=0.0025, taker=0.005)
        """

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch latest ticker for a single trading pair. e.g. 'BTC/USDT'"""

    @abstractmethod
    async def fetch_tickers(self, symbols: list[str]) -> dict[str, Ticker]:
        """Batch-fetch tickers. Returns {symbol: Ticker}."""

    @abstractmethod
    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """Fetch order book snapshot."""

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> list[Candle]:
        """
        Fetch OHLCV candles, most-recent last.
        timeframe: '1m', '5m', '15m', '1h', '4h', '1d'
        """

    # -- Private trading (authenticated) --

    async def fetch_balance(self) -> dict:
        raise NotImplementedError("fetch_balance requires authenticated credentials.")

    async def create_order(
        self,
        symbol: str,
        side: str,          # 'buy' | 'sell'
        order_type: str,    # 'limit' | 'market'
        amount: float,
        price: float | None = None,
    ) -> dict:
        raise NotImplementedError("create_order requires authenticated credentials.")

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        raise NotImplementedError("cancel_order requires authenticated credentials.")

    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        raise NotImplementedError("fetch_order requires authenticated credentials.")

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        raise NotImplementedError("fetch_open_orders requires authenticated credentials.")
