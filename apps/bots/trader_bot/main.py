"""
Trader Bot — main loop.

Responsibilities:
  - Load active strategies from the DB on every tick.
  - Dispatch each strategy to its registered handler.
  - Manage the exchange connection lifecycle.

All strategy logic lives in trader_bot/strategies/. Adding a new strategy
requires no changes here — register it in strategies/__init__.py.
"""

import asyncio
import json
import logging

from common.connections import get_exchange
from common.db import close_pool, get_conn, init_pool

from .strategies import get_strategy

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


async def load_active_strategies() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT ts.*,
                   a_base.code  AS base_asset_code,
                   a_quote.code AS quote_asset_code
            FROM base.trade_strategies ts
            JOIN base.assets a_base  ON a_base.id  = ts.base_asset_id
            JOIN base.assets a_quote ON a_quote.id = ts.quote_asset_id
            WHERE ts.status = 'ACTIVE' AND ts.deleted_at IS NULL
            """
        )
        result = []
        for r in rows:
            row = dict(r)
            if isinstance(row.get("metadata"), str):
                row["metadata"] = json.loads(row["metadata"])
            result.append(row)
        return result


async def sync_strategy_fees(exchange, strategies: list[dict]) -> None:
    """
    Fetch live maker/taker fees from the exchange for each active strategy and
    update base.trade_strategies if the values have changed.

    Runs once at bot startup so all fee calculations use real exchange rates
    rather than whatever was hardcoded when the strategy was created.
    """
    # Build unique symbols from active strategies
    symbols = {
        f"{s['base_asset_code'].upper()}/{s['quote_asset_code'].upper()}"
        for s in strategies
    }

    fee_map: dict[str, dict] = {}
    for symbol in symbols:
        try:
            fees = await exchange.fetch_trading_fees(symbol)
            fee_map[symbol] = fees
            logger.info(
                "Live fees for %s: maker=%.4f%% taker=%.4f%%",
                symbol, fees["maker"] * 100, fees["taker"] * 100,
            )
        except Exception as exc:
            logger.warning("Could not fetch fees for %s: %s", symbol, exc)

    if not fee_map:
        return

    async with get_conn() as conn:
        for strategy in strategies:
            symbol = f"{strategy['base_asset_code'].upper()}/{strategy['quote_asset_code'].upper()}"
            fees   = fee_map.get(symbol)
            if not fees:
                continue

            maker = fees["maker"]
            taker = fees["taker"]

            # Skip DB write if nothing changed
            if (
                abs(float(strategy.get("maker_fee_pct") or 0) - maker) < 1e-8
                and abs(float(strategy.get("taker_fee_pct") or 0) - taker) < 1e-8
            ):
                continue

            await conn.execute(
                """
                UPDATE base.trade_strategies
                SET maker_fee_pct = $1,
                    taker_fee_pct = $2,
                    metadata      = jsonb_set(
                                        jsonb_set(metadata, '{maker_fee_pct}', $3::text::jsonb),
                                        '{taker_fee_pct}', $4::text::jsonb
                                    ),
                    updated_at    = NOW()
                WHERE id = $5
                """,
                maker, taker,
                str(maker), str(taker),
                strategy["id"],
            )
            logger.info(
                "Strategy %d (%s): fees updated — maker=%.4f%% taker=%.4f%%",
                strategy["id"], strategy["name"], maker * 100, taker * 100,
            )

            # Keep in-memory dict in sync for the current tick
            strategy["maker_fee_pct"] = maker
            strategy["taker_fee_pct"] = taker
            if isinstance(strategy.get("metadata"), dict):
                strategy["metadata"]["maker_fee_pct"] = maker
                strategy["metadata"]["taker_fee_pct"] = taker


async def dispatch(exchange, strategy: dict) -> None:
    handler = get_strategy(strategy["strategy_type"])
    if handler is None:
        logger.warning(
            "Strategy %d: unregistered strategy_type '%s', skipping.",
            strategy["id"], strategy["strategy_type"],
        )
        return
    await handler.process(exchange, strategy)


async def run() -> None:
    await init_pool()
    exchange = get_exchange("cryptocom", paper=True)

    try:
        logger.info("Trader bot started (paper mode).")

        # Sync fees from exchange once at startup
        try:
            initial_strategies = await load_active_strategies()
            await sync_strategy_fees(exchange, initial_strategies)
        except Exception as exc:
            logger.warning("Fee sync at startup failed: %s — continuing with DB values.", exc)

        while True:
            try:
                strategies = await load_active_strategies()
                if not strategies:
                    logger.debug("No active strategies found.")
                for strategy in strategies:
                    await dispatch(exchange, strategy)
            except Exception as exc:
                logger.exception("Trader bot tick error: %s", exc)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await exchange.close()
        await close_pool()
        logger.info("Trader bot stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
