"""
CoinMarketCap REST API client.

Requires a CMC API key (Basic plan or above).
Set COINMARKETCAP_API_KEY in .env.local to enable.

Free Basic plan: 10,000 credits/month (~333 daily OHLCV calls/day).
"""

import requests

BASE_URL        = "https://pro-api.coinmarketcap.com/v2"
DEFAULT_TIMEOUT = 30


class CoinMarketCapClient:

    def __init__(self, api_key: str = "") -> None:
        if not api_key:
            raise ValueError(
                "CoinMarketCap API key is required. "
                "Set COINMARKETCAP_API_KEY in .env.local."
            )
        self._headers = {
            "X-CMC_PRO_API_KEY": api_key,
            "Accept": "application/json",
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET request with CMC auth header and timeout."""
        resp = requests.get(
            f"{BASE_URL}/{path.lstrip('/')}",
            headers=self._headers,
            params=params or {},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def get_ohlcv_historical(
        self,
        cmc_id: int | str,
        time_period: str = "daily",
        count: int = 10,
        convert: str = "USD",
    ) -> dict:
        """
        GET /cryptocurrency/ohlcv/historical

        Returns daily OHLCV candles for a CMC asset ID.
          cmc_id     : numeric CMC id (e.g. 1 for BTC, 1027 for ETH)
          time_period: 'daily' | 'hourly' | 'weekly' | 'monthly'
          count      : number of candles to return (max 10000)
          convert    : quote currency (default USD)

        Response shape:
          data.quotes[].quote.USD.{open, high, low, close, volume, market_cap}
          data.quotes[].time_open, time_close, time_high, time_low
        """
        return self._get("/cryptocurrency/ohlcv/historical", params={
            "id": cmc_id,
            "time_period": time_period,
            "count": count,
            "convert": convert,
        })

    def get_ohlcv_latest(self, cmc_id: int | str, convert: str = "USD") -> dict:
        """
        GET /cryptocurrency/ohlcv/latest
        Returns the current (incomplete) day's OHLCV for a CMC asset ID.
        Useful for intraday updates.
        """
        return self._get("/cryptocurrency/ohlcv/latest", params={
            "id": cmc_id,
            "convert": convert,
        })

    def get_quotes_latest(self, cmc_id: int | str, convert: str = "USD") -> dict:
        """
        GET /cryptocurrency/quotes/latest
        Returns current price, market cap, volume, circulating supply.
        """
        return self._get("/cryptocurrency/quotes/latest", params={
            "id": cmc_id,
            "convert": convert,
        })
