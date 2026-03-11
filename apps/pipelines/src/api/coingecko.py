"""
CoinGecko REST API client.

Handles auth header injection, rate-limit delay, and response parsing.
All methods return parsed Python objects (list/dict). Callers never touch
requests directly.

API key tiers:
  Demo (free, registration required) → x-cg-demo-api-key  ~30–50 req/min
  Pro  (paid)                        → x-cg-pro-api-key   500+ req/min

Set COINGECKO_API_KEY_TYPE=pro in .env.local when using a paid Pro key.
Defaults to demo header (safe for free-tier keys).
"""

import os
import time

import requests

BASE_URL           = "https://api.coingecko.com/api/v3"
DEFAULT_TIMEOUT    = 30
DEFAULT_DELAY      = 2.0  # seconds between requests on free/demo tier
PRO_DELAY          = 0.2  # seconds between requests on paid Pro tier

_DEMO_HEADER = "x-cg-demo-api-key"
_PRO_HEADER  = "x-cg-pro-api-key"


class CoinGeckoClient:

    def __init__(
        self,
        api_key:       str   = "",
        key_type:      str   = "",   # "pro" or "demo" (default); falls back to env COINGECKO_API_KEY_TYPE
        request_delay: float | None = None,
    ) -> None:
        resolved_type = (key_type or os.environ.get("COINGECKO_API_KEY_TYPE", "demo")).lower()
        is_pro        = resolved_type == "pro"

        if api_key:
            header = _PRO_HEADER if is_pro else _DEMO_HEADER
            self._headers = {header: api_key}
        else:
            self._headers = {}

        self._request_delay = request_delay if request_delay is not None else (
            PRO_DELAY if is_pro else DEFAULT_DELAY
        )
        self._is_pro = is_pro

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        """GET request with auth headers, timeout, and polite delay."""
        resp = requests.get(
            f"{BASE_URL}/{path.lstrip('/')}",
            headers=self._headers,
            params=params or {},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        time.sleep(self._request_delay)
        return resp.json()

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def get_coins_list(self, include_platform: bool = True) -> list[dict]:
        """
        GET /coins/list?include_platform=true

        Returns all coins supported by CoinGecko.
        Each item: {id, symbol, name, platforms: {chain: contract_address}}
        ~13,000+ coins on the free tier.
        """
        return self._get("/coins/list", params={"include_platform": str(include_platform).lower()})

    def get_asset_platforms(self) -> list[dict]:
        """
        GET /asset_platforms
        Returns all blockchain networks supported by CoinGecko.
        Each item: {id, chain_identifier, name, shortname, native_coin_id, ...}
        """
        return self._get("/asset_platforms")

    def get_ohlcv(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 90,
    ) -> list[list]:
        """
        GET /coins/{id}/ohlc?vs_currency={vs}&days={days}

        Returns [[timestamp_ms, open, high, low, close], ...] sorted ascending.

        Granularity depends on days (free tier):
          1–2   days  → 30 min candles
          3–90  days  → 4h  candles
          91+   days  → 1d  candles  ← use days >= 90 for daily OHLCV
        """
        return self._get(f"/coins/{coin_id}/ohlc", params={
            "vs_currency": vs_currency,
            "days": days,
        })

    def get_coin_history(self, coin_id: str, date: str) -> dict:
        """
        GET /coins/{id}/history?date={DD-MM-YYYY}
        Returns price, market_cap, total_volume snapshot for a specific date.
        Useful for backfilling market cap / circulating supply.
        """
        return self._get(f"/coins/{coin_id}/history", params={
            "date": date,
            "localization": "false",
        })

    def get_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 91,
    ) -> dict:
        """
        GET /coins/{id}/market_chart?vs_currency={vs}&days={days}[&interval=daily]
        Returns {prices, market_caps, total_volumes} as [[timestamp_ms, value], ...].

        Granularity on free/demo tier:
          1 day  → ~5 min interval
          2–90   → hourly
          91+    → daily  ← workaround; use days > 90 for one point per day

        On Pro tier: interval=daily is sent explicitly, so any days value returns
        daily granularity — no need for the days=91 workaround.

        NOTE: Does NOT return open/high/low — only close price, market cap, volume.
        Pair with /ohlc for full OHLCV.
        """
        params: dict = {"vs_currency": vs_currency, "days": days}
        if self._is_pro:
            params["interval"] = "daily"
        return self._get(f"/coins/{coin_id}/market_chart", params=params)
