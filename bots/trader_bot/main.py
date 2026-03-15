"""
Trader Bot — runs trading strategies for a single market pair.

Usage:
    # Paper trading (simulated fills, live market data)
    uv run --env-file configs/envs/.env.local \\
        python -m bots.trader_bot.main --market btc/usdt --paper

    # Live trading
    uv run --env-file configs/envs/.env.local \\
        python -m bots.trader_bot.main --market btc/usdt --exchange cryptocom

    # Custom poll interval
    uv run --env-file configs/envs/.env.local \\
        python -m bots.trader_bot.main --market sol/usdt --paper --poll-interval 30
"""

import argparse
import asyncio
import json
import logging

from common.connections import get_exchange
from common.db import close_pool, get_conn, init_pool

from .strategies import get_strategy

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trader bot — runs strategies for a single market pair.")
    p.add_argument(
        "--market",
        required=True,
        metavar="BASE/QUOTE",
        help="Trading pair in BASE/QUOTE format, e.g. btc/usdt",
    )
    p.add_argument(
        "--exchange",
        default="cryptocom",
        metavar="ID",
        help="Exchange ID, e.g. cryptocom, binance (default: cryptocom)",
    )
    p.add_argument(
        "--paper",
        action="store_true",
        help="Run in paper trading mode (simulated fills, live market data)",
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Poll interval in seconds (default: 60)",
    )
    args = p.parse_args()
    if "/" not in args.market:
        p.error(f"Invalid market '{args.market}' — expected BASE/QUOTE format")
    return args


async def load_active_strategies(market: str) -> list[dict]:
    base_code, quote_code = market.lower().split("/", 1)
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT ts.*,
                   a_base.code  AS base_asset_code,
                   a_quote.code AS quote_asset_code
            FROM inotives_tradings.trade_strategies ts
            JOIN inotives_tradings.assets a_base  ON a_base.id  = ts.base_asset_id
            JOIN inotives_tradings.assets a_quote ON a_quote.id = ts.quote_asset_id
            WHERE ts.status = 'ACTIVE' AND ts.deleted_at IS NULL
              AND LOWER(a_base.code)  = $1
              AND LOWER(a_quote.code) = $2
            """,
            base_code, quote_code,
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
    update inotives_tradings.trade_strategies if the values have changed.

    Runs once at bot startup so all fee calculations use real exchange rates
    rather than whatever was hardcoded when the strategy was created.
    """
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

            if (
                abs(float(strategy.get("maker_fee_pct") or 0) - maker) < 1e-8
                and abs(float(strategy.get("taker_fee_pct") or 0) - taker) < 1e-8
            ):
                continue

            await conn.execute(
                """
                UPDATE inotives_tradings.trade_strategies
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

            strategy["maker_fee_pct"] = maker
            strategy["taker_fee_pct"] = taker
            if isinstance(strategy.get("metadata"), dict):
                strategy["metadata"]["maker_fee_pct"] = maker
                strategy["metadata"]["taker_fee_pct"] = taker


async def dispatch(exchange, strategy: dict, paper: bool = False) -> None:
    handler = get_strategy(strategy["strategy_type"])
    if handler is None:
        logger.warning(
            "Strategy %d: unregistered strategy_type '%s', skipping.",
            strategy["id"], strategy["strategy_type"],
        )
        return
    await handler.process(exchange, strategy, paper=paper)


async def run(args: argparse.Namespace) -> None:
    await init_pool()
    mode = "paper" if args.paper else "live"
    exchange = get_exchange(args.exchange, paper=args.paper)

    try:
        logger.info(
            "Trader bot started — market=%s, exchange=%s, mode=%s, poll_interval=%ds",
            args.market, args.exchange, mode, args.poll_interval,
        )

        try:
            initial_strategies = await load_active_strategies(args.market)
            await sync_strategy_fees(exchange, initial_strategies)
        except Exception as exc:
            logger.warning("Fee sync at startup failed: %s — continuing with DB values.", exc)

        while True:
            try:
                strategies = await load_active_strategies(args.market)
                if not strategies:
                    logger.debug("No active strategies for %s.", args.market)
                for strategy in strategies:
                    await dispatch(exchange, strategy, paper=args.paper)
            except Exception as exc:
                logger.exception("Trader bot tick error: %s", exc)

            await asyncio.sleep(args.poll_interval)
    finally:
        await exchange.close()
        await close_pool()
        logger.info("Trader bot stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(run(parse_args()))
