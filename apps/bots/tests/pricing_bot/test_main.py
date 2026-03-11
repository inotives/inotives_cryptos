"""
Unit tests for pricing_bot.main.

Covers:
  fetch_and_store — ticker fetching, row building, DB insertion
  load_ids        — DB lookups for source_id and asset_ids

All DB interactions and exchange calls are mocked.
WATCH_PAIRS is patched in most tests to keep fixtures small and explicit.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.connections.base import Ticker
import pricing_bot.main as bot


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_ticker(
    symbol: str,
    last: float | None = 50000.0,
    bid: float = 49990.0,
    ask: float = 50010.0,
    spread_pct: float = 0.04,
    volume_24h: float = 1_000_000.0,
) -> Ticker:
    return Ticker(
        symbol=symbol, last=last, bid=bid, ask=ask,
        spread_pct=spread_pct, volume_24h=volume_24h,
        timestamp=datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc),
    )


def make_mock_conn():
    conn = AsyncMock()
    conn.fetchrow    = AsyncMock()
    conn.executemany = AsyncMock()
    return conn


def make_get_conn(conn):
    """Return a drop-in replacement for get_conn() that yields the mock conn."""
    @asynccontextmanager
    async def _get_conn():
        yield conn
    return _get_conn


SIMPLE_PAIRS = [("btc", "usdt", "BTC/USDT")]
TWO_PAIRS    = [("btc", "usdt", "BTC/USDT"), ("eth", "usdt", "ETH/USDT")]


# ---------------------------------------------------------------------------
# fetch_and_store
# ---------------------------------------------------------------------------

class TestFetchAndStore:

    @pytest.mark.asyncio
    async def test_inserts_row_for_valid_ticker(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": make_ticker("BTC/USDT", last=50000.0),
        })
        conn = make_mock_conn()

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map={"BTC/USDT": (10, 11)})

        conn.executemany.assert_awaited_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_inserts_rows_for_all_valid_pairs(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": make_ticker("BTC/USDT", last=50000.0),
            "ETH/USDT": make_ticker("ETH/USDT", last=3000.0),
        })
        conn = make_mock_conn()
        pair_map = {"BTC/USDT": (10, 11), "ETH/USDT": (20, 11)}

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map=pair_map)

        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_skips_symbol_absent_from_ticker_response(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": make_ticker("BTC/USDT"),
            # ETH/USDT missing from response
        })
        conn = make_mock_conn()
        pair_map = {"BTC/USDT": (10, 11), "ETH/USDT": (20, 11)}

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map=pair_map)

        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 1
        assert rows[0][1] == 10   # base_asset_id for BTC

    @pytest.mark.asyncio
    async def test_skips_pair_when_last_price_is_none(self):
        """Ticker present but last=None must be skipped — observed_price NOT NULL."""
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": make_ticker("BTC/USDT", last=None),
            "ETH/USDT": make_ticker("ETH/USDT", last=3000.0),
        })
        conn = make_mock_conn()
        pair_map = {"BTC/USDT": (10, 11), "ETH/USDT": (20, 11)}

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map=pair_map)

        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 1
        assert rows[0][1] == 20   # only ETH made it through

    @pytest.mark.asyncio
    async def test_executemany_not_called_when_no_valid_rows(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={})   # nothing returned
        conn = make_mock_conn()
        pair_map = {"BTC/USDT": (10, 11)}

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map=pair_map)

        # executemany still called but with 0 rows — no crash
        rows = conn.executemany.call_args[0][1]
        assert rows == []

    @pytest.mark.asyncio
    async def test_row_column_order_matches_sql(self):
        """
        Verify each row is (source_id, base_id, quote_id, last, bid, ask,
        spread_pct, volume_24h, timestamp) — matching the INSERT column list.
        """
        ts = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
        ticker = Ticker(
            symbol="BTC/USDT", last=50000.0, bid=49990.0, ask=50010.0,
            spread_pct=0.04, volume_24h=1_234_567.0, timestamp=ts,
        )
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={"BTC/USDT": ticker})
        conn = make_mock_conn()

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=7, pair_map={"BTC/USDT": (10, 11)})

        row = conn.executemany.call_args[0][1][0]
        source_id, base_id, quote_id, last, bid, ask, spread, vol24h, observed_at = row
        assert source_id  == 7
        assert base_id    == 10
        assert quote_id   == 11
        assert last       == 50000.0
        assert bid        == 49990.0
        assert ask        == 50010.0
        assert spread     == 0.04
        assert vol24h     == 1_234_567.0
        assert observed_at == ts

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_nothing_clause(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={"BTC/USDT": make_ticker("BTC/USDT")})
        conn = make_mock_conn()

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map={"BTC/USDT": (10, 11)})

        sql = conn.executemany.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    @pytest.mark.asyncio
    async def test_fetch_tickers_called_with_all_pair_symbols(self):
        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={})
        conn = make_mock_conn()
        pair_map = {"BTC/USDT": (10, 11), "ETH/USDT": (20, 11), "SOL/USDT": (30, 11)}

        with patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            await bot.fetch_and_store(exchange, source_id=1, pair_map=pair_map)

        called_symbols = exchange.fetch_tickers.call_args[0][0]
        assert set(called_symbols) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}


# ---------------------------------------------------------------------------
# load_ids
# ---------------------------------------------------------------------------

class TestLoadIds:

    def _make_row(self, id_val):
        """Minimal asyncpg-like row mock."""
        row = MagicMock()
        row.__getitem__ = lambda self, key: id_val
        return row

    @pytest.mark.asyncio
    async def test_returns_source_id_and_pair_map(self):
        conn = make_mock_conn()
        # fetchrow call order: source → btc → usdt
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),    # source_id = 5
            self._make_row(10),   # base_asset_id (btc) = 10
            self._make_row(11),   # quote_asset_id (usdt) = 11
        ])

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            source_id, pair_map = await bot.load_ids("exchange:cryptocom")

        assert source_id == 5
        assert pair_map == {"BTC/USDT": (10, 11)}

    @pytest.mark.asyncio
    async def test_raises_when_source_not_found(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)   # source lookup returns nothing

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            with pytest.raises(ValueError, match="not found"):
                await bot.load_ids("exchange:unknown")

    @pytest.mark.asyncio
    async def test_skips_pair_when_base_asset_not_in_db(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),   # source
            None,                # base asset missing
            self._make_row(11),  # quote (would be skipped anyway)
        ])

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            _, pair_map = await bot.load_ids("exchange:cryptocom")

        assert pair_map == {}

    @pytest.mark.asyncio
    async def test_skips_pair_when_quote_asset_not_in_db(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),   # source
            self._make_row(10),  # base asset found
            None,                # quote asset missing
        ])

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            _, pair_map = await bot.load_ids("exchange:cryptocom")

        assert pair_map == {}

    @pytest.mark.asyncio
    async def test_partial_pairs_loaded_when_some_assets_missing(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),   # source
            self._make_row(10),  # btc base
            self._make_row(11),  # usdt quote  → BTC/USDT OK
            None,                # eth base missing
            self._make_row(11),  # usdt quote  → ETH/USDT skipped
        ])

        with patch.object(bot, "WATCH_PAIRS", TWO_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            _, pair_map = await bot.load_ids("exchange:cryptocom")

        assert "BTC/USDT" in pair_map
        assert "ETH/USDT" not in pair_map

    @pytest.mark.asyncio
    async def test_returns_empty_pair_map_when_no_assets_found(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),   # source found
            None, None,          # btc, usdt both missing
        ])

        with patch.object(bot, "WATCH_PAIRS", SIMPLE_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            _, pair_map = await bot.load_ids("exchange:cryptocom")

        assert pair_map == {}

    @pytest.mark.asyncio
    async def test_builds_correct_pair_map_for_multiple_pairs(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(side_effect=[
            self._make_row(5),   # source
            self._make_row(10),  # btc base
            self._make_row(11),  # usdt quote
            self._make_row(20),  # eth base
            self._make_row(11),  # usdt quote
        ])

        with patch.object(bot, "WATCH_PAIRS", TWO_PAIRS), \
             patch("pricing_bot.main.get_conn", make_get_conn(conn)):
            source_id, pair_map = await bot.load_ids("exchange:cryptocom")

        assert source_id == 5
        assert pair_map["BTC/USDT"] == (10, 11)
        assert pair_map["ETH/USDT"] == (20, 11)
