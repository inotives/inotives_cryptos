"""
Generic ccxt REST implementation of BaseExchangeConnection.

Works for any exchange supported by ccxt. Per-exchange subclasses in
exchanges/ only need to override methods where behaviour differs.
"""

from datetime import datetime, timezone

import ccxt.async_support as ccxt

from .base import BaseExchangeConnection, Candle, OrderBook, Ticker, TradingFees


class CcxtRestConnection(BaseExchangeConnection):

    def __init__(self, exchange_id: str, api_key: str = "", secret: str = "") -> None:
        params: dict = {"enableRateLimit": True}
        if api_key:
            params["apiKey"] = api_key
        if secret:
            params["secret"] = secret

        exchange_cls = getattr(ccxt, exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Exchange '{exchange_id}' is not supported by ccxt.")

        self._exchange: ccxt.Exchange = exchange_cls(params)
        self.exchange_id = exchange_id

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._exchange.close()

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _ts(ms: int | None) -> datetime:
        """Convert ccxt millisecond timestamp to UTC datetime."""
        if ms is None:
            return datetime.now(timezone.utc)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    @staticmethod
    def _spread(bid: float | None, ask: float | None) -> float | None:
        if bid and ask and bid > 0:
            return round((ask - bid) / bid * 100, 6)
        return None

    def _normalise_ticker(self, raw: dict) -> Ticker:
        bid = raw.get("bid")
        ask = raw.get("ask")
        # Some exchanges don't return quoteVolume on public endpoints —
        # fall back to baseVolume * last as an approximation
        quote_vol = raw.get("quoteVolume")
        if quote_vol is None:
            base_vol = raw.get("baseVolume")
            last     = raw.get("last")
            if base_vol and last:
                quote_vol = round(base_vol * last, 2)

        return Ticker(
            symbol     = raw["symbol"],
            last       = raw.get("last"),
            bid        = bid,
            ask        = ask,
            spread_pct = self._spread(bid, ask),
            volume_24h = quote_vol,
            timestamp  = self._ts(raw.get("timestamp")),
        )

    # ── Public market data ─────────────────────────────────────────────────────

    async def fetch_trading_fees(self, symbol: str) -> TradingFees:
        if not self._exchange.markets:
            await self._exchange.load_markets()
        market = self._exchange.markets.get(symbol, {})
        return TradingFees(
            symbol = symbol,
            maker  = float(market.get("maker") or 0.0),
            taker  = float(market.get("taker") or 0.0),
        )

    async def fetch_ticker(self, symbol: str) -> Ticker:
        raw = await self._exchange.fetch_ticker(symbol)
        return self._normalise_ticker(raw)

    async def fetch_tickers(self, symbols: list[str]) -> dict[str, Ticker]:
        raw_map = await self._exchange.fetch_tickers(symbols)
        return {sym: self._normalise_ticker(raw) for sym, raw in raw_map.items()}

    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        raw = await self._exchange.fetch_order_book(symbol, limit=limit)
        return OrderBook(
            symbol    = symbol,
            bids      = [tuple(b[:2]) for b in raw["bids"]],
            asks      = [tuple(a[:2]) for a in raw["asks"]],
            timestamp = self._ts(raw.get("timestamp")),
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> list[Candle]:
        raw = await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [
            Candle(
                timestamp = self._ts(row[0]),
                open      = row[1],
                high      = row[2],
                low       = row[3],
                close     = row[4],
                volume    = row[5],
            )
            for row in raw
        ]

    # ── Private trading (authenticated) ───────────────────────────────────────

    async def fetch_balance(self) -> dict:
        return await self._exchange.fetch_balance()

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
    ) -> dict:
        return await self._exchange.create_order(symbol, order_type, side, amount, price)

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        return await self._exchange.cancel_order(order_id, symbol)

    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        return await self._exchange.fetch_order(order_id, symbol)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        return await self._exchange.fetch_open_orders(symbol)
