from .base import BaseExchangeConnection, Ticker, OrderBook, Candle
from .ccxt_rest import CcxtRestConnection
from .exchanges.cryptocom import CryptoComConnection
from .paper import PaperTradingConnection


def get_exchange(
    exchange_id: str,
    api_key: str = "",
    secret: str = "",
    paper: bool = False,
    initial_balance: float = 10_000.0,
) -> BaseExchangeConnection:
    """
    Factory — returns the right connection instance for a given exchange ID.

    Args:
        exchange_id:     ccxt exchange identifier, e.g. 'cryptocom', 'binance'
        api_key:         API key (optional — public endpoints work without it)
        secret:          API secret
        paper:           If True, wraps the exchange in PaperTradingConnection.
                         Market data is live; all order operations are simulated.
        initial_balance: Starting quote balance for paper account (default 10,000 USDT).

    Usage:
        exchange = get_exchange("cryptocom")                            # live, public only
        exchange = get_exchange("cryptocom", paper=True)                # paper trading
        exchange = get_exchange("cryptocom", api_key=..., secret=...)   # live, authenticated
    """
    registry: dict[str, type[BaseExchangeConnection]] = {
        "cryptocom": CryptoComConnection,
    }

    cls = registry.get(exchange_id)
    real: BaseExchangeConnection = (
        cls(api_key=api_key, secret=secret)
        if cls is not None
        else CcxtRestConnection(exchange_id, api_key=api_key, secret=secret)
    )

    if paper:
        return PaperTradingConnection(real, initial_balance=initial_balance)

    return real
