"""
Crypto.com (cryptocom) connection.

Quirks handled:
  - fetch_tickers() on public endpoint doesn't support symbol filtering —
    fetches all and filters client-side.
  - quoteVolume is None on public ticker — approximated in base class.
"""

from ..ccxt_rest import CcxtRestConnection
from ..base import Ticker


class CryptoComConnection(CcxtRestConnection):

    def __init__(self, api_key: str = "", secret: str = "") -> None:
        super().__init__("cryptocom", api_key=api_key, secret=secret)

    async def fetch_tickers(self, symbols: list[str]) -> dict[str, Ticker]:
        # Crypto.com public endpoint returns all tickers at once —
        # pass symbols=None to avoid a 'not supported' error, then filter.
        raw_map = await self._exchange.fetch_tickers()
        result = {}
        for sym in symbols:
            if sym in raw_map:
                result[sym] = self._normalise_ticker(raw_map[sym])
        return result
