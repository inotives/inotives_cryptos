"""
Fetch token lists from CoinGecko and optionally save to database.

Usage:
    # Print as JSON (default):
    python -m common.tools.fetch_token_list

    # Save to database:
    python -m common.tools.fetch_token_list --output db

    # Pretty print table:
    python -m common.tools.fetch_token_list --output table

    # Top 10 tokens:
    python -m common.tools.fetch_token_list --top 10

    # Filter by category:
    python -m common.tools.fetch_token_list --category "Layer 1"

    # Custom parameters:
    python -m common.tools.fetch_token_list -v usd -p 50 --page 2 --order volume_desc
"""

import argparse
import asyncio
import json

from common.api.coingecko import CoinGeckoClient
from common.config import settings
from common.db import get_conn, init_pool, close_pool, is_pool_initialized


async def ensure_table_exists(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS inotives_tradings.token_list_cache (
            coin_id TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            image TEXT,
            current_price NUMERIC(36, 18),
            market_cap NUMERIC(36, 2),
            market_cap_rank INTEGER,
            total_volume NUMERIC(36, 2),
            high_24h NUMERIC(36, 18),
            low_24h NUMERIC(36, 18),
            price_change_24h NUMERIC(36, 18),
            price_change_percentage_24h NUMERIC(10, 6),
            market_cap_change_24h NUMERIC(36, 2),
            market_cap_change_percentage_24h NUMERIC(10, 6),
            circulating_supply NUMERIC(36, 2),
            total_supply NUMERIC(36, 2),
            max_supply NUMERIC(36, 2),
            ath NUMERIC(36, 18),
            ath_change_percentage NUMERIC(10, 6),
            ath_date TIMESTAMPTZ,
            atl NUMERIC(36, 18),
            atl_change_percentage NUMERIC(10, 6),
            atl_date TIMESTAMPTZ,
            last_updated TIMESTAMPTZ
        )
    """)


async def upsert_tokens(conn, tokens: list[dict]) -> int:
    if not tokens:
        return 0

    await ensure_table_exists(conn)

    upsert_sql = """
        INSERT INTO inotives_tradings.token_list_cache (
            coin_id, symbol, name, image, current_price, market_cap, market_cap_rank,
            total_volume, high_24h, low_24h, price_change_24h, price_change_percentage_24h,
            market_cap_change_24h, market_cap_change_percentage_24h, circulating_supply,
            total_supply, max_supply, ath, ath_change_percentage, ath_date,
            atl, atl_change_percentage, atl_date, last_updated
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)
        ON CONFLICT (coin_id) DO UPDATE SET
            symbol = EXCLUDED.symbol,
            name = EXCLUDED.name,
            image = EXCLUDED.image,
            current_price = EXCLUDED.current_price,
            market_cap = EXCLUDED.market_cap,
            market_cap_rank = EXCLUDED.market_cap_rank,
            total_volume = EXCLUDED.total_volume,
            high_24h = EXCLUDED.high_24h,
            low_24h = EXCLUDED.low_24h,
            price_change_24h = EXCLUDED.price_change_24h,
            price_change_percentage_24h = EXCLUDED.price_change_percentage_24h,
            market_cap_change_24h = EXCLUDED.market_cap_change_24h,
            market_cap_change_percentage_24h = EXCLUDED.market_cap_change_percentage_24h,
            circulating_supply = EXCLUDED.circulating_supply,
            total_supply = EXCLUDED.total_supply,
            max_supply = EXCLUDED.max_supply,
            ath = EXCLUDED.ath,
            ath_change_percentage = EXCLUDED.ath_change_percentage,
            ath_date = EXCLUDED.ath_date,
            atl = EXCLUDED.atl,
            atl_change_percentage = EXCLUDED.atl_change_percentage,
            atl_date = EXCLUDED.atl_date,
            last_updated = EXCLUDED.last_updated
    """

    for token in tokens:
        await conn.execute(
            upsert_sql,
            token.get("id"),
            token.get("symbol"),
            token.get("name"),
            token.get("image"),
            token.get("current_price"),
            token.get("market_cap"),
            token.get("market_cap_rank"),
            token.get("total_volume"),
            token.get("high_24h"),
            token.get("low_24h"),
            token.get("price_change_24h"),
            token.get("price_change_percentage_24h"),
            token.get("market_cap_change_24h"),
            token.get("market_cap_change_percentage_24h"),
            token.get("circulating_supply"),
            token.get("total_supply"),
            token.get("max_supply"),
            token.get("ath"),
            token.get("ath_change_percentage"),
            token.get("ath_date"),
            token.get("atl"),
            token.get("atl_change_percentage"),
            token.get("atl_date"),
            token.get("last_updated"),
        )

    return len(tokens)


def print_table(tokens: list[dict]) -> None:
    if not tokens:
        print("No tokens found.")
        return

    headers = ["Rank", "Symbol", "Name", "Price (USD)", "Market Cap", "24h Change"]
    col_widths = [6, 10, 20, 15, 18, 12]

    header_row = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_row)
    print("-" * len(header_row))

    for token in tokens:
        rank = str(token.get("market_cap_rank", "N/A"))
        symbol = (token.get("symbol") or "").upper()[:10]
        name = (token.get("name") or "")[:20]
        price = (
            f"${token.get('current_price', 0):,.2f}"
            if token.get("current_price")
            else "N/A"
        )
        market_cap = (
            f"${token.get('market_cap', 0):,.0f}" if token.get("market_cap") else "N/A"
        )
        change = token.get("price_change_percentage_24h")
        change_str = f"{change:+.2f}%" if change is not None else "N/A"

        row = " | ".join(
            [
                rank.ljust(col_widths[0]),
                symbol.ljust(col_widths[1]),
                name.ljust(col_widths[2]),
                price.ljust(col_widths[3]),
                market_cap.ljust(col_widths[4]),
                change_str.ljust(col_widths[5]),
            ]
        )
        print(row)


async def run(args: argparse.Namespace) -> None:
    client = CoinGeckoClient(
        api_key=settings.coingecko_api_key,
        key_type=settings.coingecko_api_key_type,
    )

    params = {
        "vs_currency": args.vs_currency,
        "per_page": min(args.per_page, 250),
        "page": args.page,
        "order": args.order,
        "sparkline": str(args.sparkline).lower(),
    }

    if args.category:
        params["category"] = args.category

    tokens = client._get("/coins/markets", params=params)

    if args.top and args.top > 0:
        tokens = tokens[: args.top]

    if args.output == "json":
        print(json.dumps(tokens, indent=2, default=str))
    elif args.output == "table":
        print_table(tokens)
    elif args.output == "db":
        pool_was_initialized = is_pool_initialized()
        if not pool_was_initialized:
            await init_pool()

        try:
            async with get_conn() as conn:
                count = await upsert_tokens(conn, tokens)
                print(
                    f"✓ Upserted {count} tokens to inotives_tradings.token_list_cache"
                )
        finally:
            if not pool_was_initialized:
                await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch token lists from CoinGecko and optionally save to database."
    )
    parser.add_argument(
        "--vs-currency",
        "-v",
        default="usd",
        help="The vs currency (default: usd)",
    )
    parser.add_argument(
        "--per-page",
        "-p",
        type=int,
        default=100,
        help="Results per page (max 250, default: 100)",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="Page number (default: 1)",
    )
    parser.add_argument(
        "--order",
        default="market_cap_desc",
        choices=[
            "market_cap_desc",
            "gecko_desc",
            "gecko_asc",
            "market_cap_asc",
            "volume_asc",
            "volume_desc",
            "id_asc",
            "id_desc",
        ],
        help="Sort order (default: market_cap_desc)",
    )
    parser.add_argument(
        "--sparkline",
        action="store_true",
        help="Include sparkline data (default: false)",
    )
    parser.add_argument(
        "--category",
        default=None,
        help='Filter by category (e.g., "Layer 1", "DeFi")',
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Only return top N tokens by market cap",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="json",
        choices=["json", "db", "table"],
        help='Output format: "json" (stdout), "db" (database), "table" (pretty print)',
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
