"""
CLI for managing allow-listed assets, networks, and source mappings.

Supports two modes:
  1. Interactive (no arguments) — menu-driven TUI
  2. Non-interactive (subcommands) — scriptable for automation / OpenClaw

Non-interactive usage:
    python -m common.tools.manage_assets list-assets
    python -m common.tools.manage_assets list-assets --json
    python -m common.tools.manage_assets view-asset --asset-id 5
    python -m common.tools.manage_assets view-asset --code btc
    python -m common.tools.manage_assets add-asset --coingecko-id bitcoin
    python -m common.tools.manage_assets add-asset --coingecko-id ethereum --cmc-id 1027
    python -m common.tools.manage_assets add-asset --coingecko-id bitcoin --dry-run
    python -m common.tools.manage_assets remove-asset --asset-id 5
    python -m common.tools.manage_assets search-coins --query dog
    python -m common.tools.manage_assets list-networks
    python -m common.tools.manage_assets add-network --coingecko-id ethereum
    python -m common.tools.manage_assets remove-network --network-id 3
    python -m common.tools.manage_assets list-sources
    python -m common.tools.manage_assets list-mappings --asset-id 5
    python -m common.tools.manage_assets pricing-pairs
    python -m common.tools.manage_assets pricing-pairs --exchange-id cryptocom --json

Interactive usage:
    python -m common.tools.manage_assets
    python -m common.tools.manage_assets interactive
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

import asyncpg

from common.config import settings

# ── ANSI colours ──────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
WHITE  = "\033[97m"


def c(text, colour):  return f"{colour}{text}{RESET}"
def bold(text):       return c(text, BOLD)
def header(text):     print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}\n{BOLD}{CYAN}  {text}{RESET}\n{DIM}{'─' * 50}{RESET}")
def success(text):    print(f"  {GREEN}✓ {text}{RESET}")
def warn(text):       print(f"  {YELLOW}⚠ {text}{RESET}")
def error(text):      print(f"  {RED}✗ {text}{RESET}")
def info(text):       print(f"  {DIM}{text}{RESET}")


# ── DB ────────────────────────────────────────────────────────────────────────

async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.db_dsn)


# ── Input helpers (interactive mode) ──────────────────────────────────────────

def prompt(label: str, default: str = "") -> str:
    dflt = f" [{default}]" if default else ""
    val  = input(f"  {label}{dflt}: ").strip()
    return val if val else default

def prompt_int(label: str, default: int | None = None) -> int | None:
    dflt = str(default) if default is not None else ""
    raw  = prompt(label, dflt)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default

def prompt_bool(label: str, default: bool = True) -> bool:
    dflt = "Y/n" if default else "y/N"
    raw  = prompt(f"{label} ({dflt})", "").lower()
    if raw in ("y", "yes"):  return True
    if raw in ("n", "no"):   return False
    return default

def menu(options: list[str]) -> str:
    for i, opt in enumerate(options, 1):
        print(f"  {BOLD}{i}{RESET}. {opt}")
    print(f"  {DIM}0. Back / Exit{RESET}")
    return input(f"\n  {BOLD}Choice:{RESET} ").strip()


# ── Constants ─────────────────────────────────────────────────────────────────

COINGECKO_SOURCE_CODE = "api:coingecko"
CMC_SOURCE_CODE       = "api:coinmarketcap"


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED QUERIES
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_assets(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT a.id, a.code, a.name, a.symbol, a.type, a.is_origin_asset,
               n.code AS network_code,
               COUNT(asm.id) AS mapping_count
        FROM inotives_tradings.assets a
        LEFT JOIN inotives_tradings.networks n ON n.id = a.network_id
        LEFT JOIN inotives_tradings.asset_source_mappings asm
            ON asm.asset_id = a.id AND asm.deleted_at IS NULL
        WHERE a.deleted_at IS NULL
        GROUP BY a.id, a.code, a.name, a.symbol, a.type, a.is_origin_asset, n.code
        ORDER BY a.code
        """
    )
    return [dict(r) for r in rows]


async def _fetch_asset_detail(conn: asyncpg.Connection, asset_id: int = None, code: str = None) -> dict | None:
    if asset_id:
        row = await conn.fetchrow(
            """
            SELECT a.*, n.code AS network_code, n.name AS network_name
            FROM inotives_tradings.assets a
            LEFT JOIN inotives_tradings.networks n ON n.id = a.network_id
            WHERE a.id = $1 AND a.deleted_at IS NULL
            """,
            asset_id,
        )
    elif code:
        row = await conn.fetchrow(
            """
            SELECT a.*, n.code AS network_code, n.name AS network_name
            FROM inotives_tradings.assets a
            LEFT JOIN inotives_tradings.networks n ON n.id = a.network_id
            WHERE LOWER(a.code) = $1 AND a.deleted_at IS NULL
            """,
            code.lower(),
        )
    else:
        return None
    return dict(row) if row else None


async def _fetch_asset_mappings(conn: asyncpg.Connection, asset_id: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT asm.id, asm.source_identifier, asm.source_symbol, asm.source_name,
               ds.source_code, ds.provider_name
        FROM inotives_tradings.asset_source_mappings asm
        JOIN inotives_tradings.data_sources ds ON ds.id = asm.source_id
        WHERE asm.asset_id = $1 AND asm.deleted_at IS NULL
        ORDER BY ds.source_code
        """,
        asset_id,
    )
    return [dict(r) for r in rows]


async def _add_asset(
    conn: asyncpg.Connection,
    coingecko_id: str,
    cmc_id: int | None = None,
    code_override: str | None = None,
    name_override: str | None = None,
) -> tuple[bool, str, dict | None]:
    """
    Allow-list a CoinGecko coin. Returns (success, message, asset_info).
    """
    coin = await conn.fetchrow(
        "SELECT coingecko_id, symbol, name FROM coingecko.raw_coins WHERE coingecko_id = $1",
        coingecko_id,
    )
    if not coin:
        return False, f"'{coingecko_id}' not found in coingecko.raw_coins. Run sync-coingecko-coins first.", None

    code = (code_override or coin["symbol"]).upper()
    name = name_override or coin["name"]

    cg_source_id = await conn.fetchval(
        "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1",
        COINGECKO_SOURCE_CODE,
    )
    if not cg_source_id:
        return False, f"Data source '{COINGECKO_SOURCE_CODE}' not found. Run seed-data-sources first.", None

    asset_id = await conn.fetchval(
        """
        INSERT INTO inotives_tradings.assets (code, name, symbol, type, is_origin_asset)
        VALUES ($1, $2, $3, 'crypto', true)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name, symbol = EXCLUDED.symbol, updated_at = NOW()
        RETURNING id
        """,
        code, name, code,
    )

    await conn.execute(
        """
        INSERT INTO inotives_tradings.asset_source_mappings
            (asset_id, source_id, source_identifier, source_symbol, source_name)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (asset_id, source_id) DO UPDATE SET
            source_identifier = EXCLUDED.source_identifier,
            source_symbol = EXCLUDED.source_symbol,
            source_name = EXCLUDED.source_name,
            updated_at = NOW()
        """,
        asset_id, cg_source_id,
        coin["coingecko_id"], coin["symbol"], coin["name"],
    )

    if cmc_id:
        cmc_source_id = await conn.fetchval(
            "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1",
            CMC_SOURCE_CODE,
        )
        if cmc_source_id:
            await conn.execute(
                """
                INSERT INTO inotives_tradings.asset_source_mappings
                    (asset_id, source_id, source_identifier, source_symbol, source_name)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (asset_id, source_id) DO UPDATE SET
                    source_identifier = EXCLUDED.source_identifier,
                    source_symbol = EXCLUDED.source_symbol,
                    source_name = EXCLUDED.source_name,
                    updated_at = NOW()
                """,
                asset_id, cmc_source_id,
                str(cmc_id), coin["symbol"], coin["name"],
            )

    asset_info = {"id": asset_id, "code": code, "name": name, "coingecko_id": coingecko_id}
    return True, f"Asset '{code}' (id={asset_id}) allow-listed from coingecko:{coingecko_id}.", asset_info


async def _remove_asset(conn: asyncpg.Connection, asset_id: int) -> tuple[bool, str]:
    """Soft-delete an asset. Returns (success, message)."""
    row = await conn.fetchrow(
        "SELECT id, code, name FROM inotives_tradings.assets WHERE id=$1 AND deleted_at IS NULL",
        asset_id,
    )
    if not row:
        return False, f"Asset {asset_id} not found."

    # Check if any active strategies reference this asset
    active = await conn.fetchval(
        """
        SELECT COUNT(*) FROM inotives_tradings.trade_strategies
        WHERE (base_asset_id = $1 OR quote_asset_id = $1)
          AND status = 'ACTIVE' AND deleted_at IS NULL
        """,
        asset_id,
    )
    if active > 0:
        return False, f"Asset has {active} active strategy(ies). Pause or archive them first."

    await conn.execute("DELETE FROM inotives_tradings.assets WHERE id=$1", asset_id)
    return True, f"Asset {asset_id} ({row['code']}) soft-deleted."


async def _search_coins(conn: asyncpg.Connection, query: str, limit: int = 20) -> list[dict]:
    """Search coingecko.raw_coins by name, symbol, or coingecko_id."""
    rows = await conn.fetch(
        """
        SELECT coingecko_id, symbol, name
        FROM coingecko.raw_coins
        WHERE coingecko_id ILIKE $1
           OR symbol ILIKE $1
           OR name ILIKE $1
        ORDER BY
            CASE WHEN symbol ILIKE $2 THEN 0 ELSE 1 END,
            name
        LIMIT $3
        """,
        f"%{query}%", query, limit,
    )
    return [dict(r) for r in rows]


async def _fetch_networks(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, code, name, category::text AS category
        FROM inotives_tradings.networks
        WHERE deleted_at IS NULL
        ORDER BY code
        """
    )
    return [dict(r) for r in rows]


async def _add_network(
    conn: asyncpg.Connection,
    coingecko_id: str,
    code_override: str | None = None,
    name_override: str | None = None,
    category: str = "blockchain",
) -> tuple[bool, str, dict | None]:
    """Allow-list a CoinGecko platform."""
    platform = await conn.fetchrow(
        """
        SELECT coingecko_id, name, shortname, chain_identifier, native_coin_id
        FROM coingecko.raw_platforms WHERE coingecko_id = $1
        """,
        coingecko_id,
    )
    if not platform:
        return False, f"'{coingecko_id}' not found in coingecko.raw_platforms. Run sync-coingecko-platforms first.", None

    code = (code_override or platform["shortname"] or platform["coingecko_id"]).upper()
    name = name_override or platform["name"]

    cg_source_id = await conn.fetchval(
        "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1",
        COINGECKO_SOURCE_CODE,
    )
    if not cg_source_id:
        return False, f"Data source '{COINGECKO_SOURCE_CODE}' not found.", None

    network_id = await conn.fetchval(
        """
        INSERT INTO inotives_tradings.networks (code, name, category)
        VALUES ($1, $2, $3::inotives_tradings.network_category)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name, category = EXCLUDED.category, updated_at = NOW()
        RETURNING id
        """,
        code, name, category,
    )

    await conn.execute(
        """
        INSERT INTO inotives_tradings.network_source_mappings
            (network_id, source_id, source_identifier, source_name, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT (network_id, source_id) DO UPDATE SET
            source_identifier = EXCLUDED.source_identifier,
            source_name = EXCLUDED.source_name,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        network_id, cg_source_id,
        platform["coingecko_id"], platform["name"],
        json.dumps({
            "chain_identifier": platform["chain_identifier"],
            "native_coin_id": platform["native_coin_id"],
        }),
    )

    info = {"id": network_id, "code": code, "name": name, "coingecko_id": coingecko_id}
    return True, f"Network '{code}' (id={network_id}) allow-listed.", info


async def _remove_network(conn: asyncpg.Connection, network_id: int) -> tuple[bool, str]:
    row = await conn.fetchrow(
        "SELECT id, code, name FROM inotives_tradings.networks WHERE id=$1 AND deleted_at IS NULL",
        network_id,
    )
    if not row:
        return False, f"Network {network_id} not found."
    # Check if any assets reference this network
    refs = await conn.fetchval(
        "SELECT COUNT(*) FROM inotives_tradings.assets WHERE network_id=$1 AND deleted_at IS NULL",
        network_id,
    )
    if refs > 0:
        return False, f"Network has {refs} asset(s) referencing it. Remove them first."
    await conn.execute("DELETE FROM inotives_tradings.networks WHERE id=$1", network_id)
    return True, f"Network {network_id} ({row['code']}) soft-deleted."


async def _fetch_data_sources(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, source_code, provider_name, category::text AS category,
               tier_name, rate_limit_rpm
        FROM inotives_tradings.data_sources
        WHERE deleted_at IS NULL
        ORDER BY source_code
        """
    )
    return [dict(r) for r in rows]


async def _build_pricing_pairs(conn: asyncpg.Connection, exchange_source_code: str = None) -> list[dict]:
    """
    Build the list of pricing bot pairs from allow-listed assets.
    All allow-listed crypto assets paired against each allow-listed quote asset
    (assets with code in common quote list: USDT, USDC, USD, etc.).

    If exchange_source_code is provided, only include assets that have a
    source mapping for that exchange.
    """
    # Get all allow-listed crypto assets
    assets = await conn.fetch(
        """
        SELECT id, code, name FROM inotives_tradings.assets
        WHERE type = 'crypto' AND deleted_at IS NULL
        ORDER BY code
        """
    )

    # Common quote assets
    quote_codes = {"usdt", "usdc", "usd"}
    base_assets = [dict(a) for a in assets if a["code"].lower() not in quote_codes]
    quote_assets = [dict(a) for a in assets if a["code"].lower() in quote_codes]

    pairs = []
    for base in base_assets:
        for quote in quote_assets:
            pair_str = f"{base['code'].lower()}/{quote['code'].lower()}"
            pairs.append({
                "pair": pair_str,
                "symbol": f"{base['code'].upper()}/{quote['code'].upper()}",
                "base_asset_id": base["id"],
                "base_code": base["code"],
                "quote_asset_id": quote["id"],
                "quote_code": quote["code"],
            })

    return pairs


# ══════════════════════════════════════════════════════════════════════════════
#  NON-INTERACTIVE SUBCOMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_list_assets(args) -> int:
    conn = await connect()
    try:
        rows = await _fetch_assets(conn)
        if not rows:
            print("No allow-listed assets found.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'ID':<5} {'Code':<8} {'Name':<20} {'Symbol':<8} {'Type':<8} {'Network':<10} {'Mappings'}")
            print("─" * 70)
            for r in rows:
                print(
                    f"{r['id']:<5} {r['code']:<8} {r['name']:<20} {r['symbol']:<8} "
                    f"{r['type']:<8} {r['network_code'] or '—':<10} {r['mapping_count']}"
                )
    finally:
        await conn.close()
    return 0


async def cmd_view_asset(args) -> int:
    conn = await connect()
    try:
        r = await _fetch_asset_detail(conn, asset_id=args.asset_id, code=args.code)
        if not r:
            target = f"id={args.asset_id}" if args.asset_id else f"code={args.code}"
            print(f"Asset {target} not found.", file=sys.stderr)
            return 1

        mappings = await _fetch_asset_mappings(conn, r["id"])

        if args.json:
            output = {
                "id": r["id"], "code": r["code"], "name": r["name"],
                "symbol": r["symbol"], "type": r["type"],
                "network": r.get("network_code"),
                "is_origin_asset": r["is_origin_asset"],
                "created_at": str(r["created_at"]),
                "mappings": mappings,
            }
            print(json.dumps(output, default=str, indent=2))
        else:
            print(f"ID:          {r['id']}")
            print(f"Code:        {r['code']}")
            print(f"Name:        {r['name']}")
            print(f"Symbol:      {r['symbol']}")
            print(f"Type:        {r['type']}")
            print(f"Network:     {r.get('network_code') or '—'}")
            print(f"Origin:      {r['is_origin_asset']}")
            print(f"Created:     {r['created_at'].strftime('%Y-%m-%d %H:%M')}")
            if mappings:
                print(f"\nSource Mappings:")
                for m in mappings:
                    print(f"  {m['source_code']:<25} → {m['source_identifier']}  ({m['provider_name']})")
            else:
                print("\nNo source mappings.")
    finally:
        await conn.close()
    return 0


async def cmd_add_asset(args) -> int:
    conn = await connect()
    try:
        if args.dry_run:
            coin = await conn.fetchrow(
                "SELECT coingecko_id, symbol, name FROM coingecko.raw_coins WHERE coingecko_id = $1",
                args.coingecko_id,
            )
            if not coin:
                print(f"'{args.coingecko_id}' not found in coingecko.raw_coins.", file=sys.stderr)
                return 1
            code = (args.code or coin["symbol"]).upper()
            name = args.name or coin["name"]
            print(f"DRY RUN — would allow-list:")
            print(f"  coingecko_id: {coin['coingecko_id']}")
            print(f"  code:         {code}")
            print(f"  name:         {name}")
            if args.cmc_id:
                print(f"  cmc_id:       {args.cmc_id}")
            return 0

        ok, msg, info = await _add_asset(
            conn, args.coingecko_id,
            cmc_id=args.cmc_id,
            code_override=args.code,
            name_override=args.name,
        )
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(info, default=str))
        else:
            print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_remove_asset(args) -> int:
    conn = await connect()
    try:
        ok, msg = await _remove_asset(conn, args.asset_id)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_search_coins(args) -> int:
    conn = await connect()
    try:
        rows = await _search_coins(conn, args.query, limit=args.limit)
        if not rows:
            print(f"No coins matching '{args.query}'.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'CoinGecko ID':<30} {'Symbol':<10} {'Name'}")
            print("─" * 60)
            for r in rows:
                print(f"{r['coingecko_id']:<30} {r['symbol']:<10} {r['name']}")
    finally:
        await conn.close()
    return 0


async def cmd_list_networks(args) -> int:
    conn = await connect()
    try:
        rows = await _fetch_networks(conn)
        if not rows:
            print("No allow-listed networks found.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'ID':<5} {'Code':<12} {'Name':<25} {'Category'}")
            print("─" * 50)
            for r in rows:
                print(f"{r['id']:<5} {r['code']:<12} {r['name']:<25} {r['category']}")
    finally:
        await conn.close()
    return 0


async def cmd_add_network(args) -> int:
    conn = await connect()
    try:
        if args.dry_run:
            platform = await conn.fetchrow(
                "SELECT coingecko_id, name, shortname FROM coingecko.raw_platforms WHERE coingecko_id = $1",
                args.coingecko_id,
            )
            if not platform:
                print(f"'{args.coingecko_id}' not found in coingecko.raw_platforms.", file=sys.stderr)
                return 1
            code = (args.code or platform["shortname"] or platform["coingecko_id"]).upper()
            name = args.name or platform["name"]
            print(f"DRY RUN — would allow-list:")
            print(f"  coingecko_id: {platform['coingecko_id']}")
            print(f"  code:         {code}")
            print(f"  name:         {name}")
            print(f"  category:     {args.category}")
            return 0

        ok, msg, info = await _add_network(
            conn, args.coingecko_id,
            code_override=args.code,
            name_override=args.name,
            category=args.category,
        )
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(info, default=str))
        else:
            print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_remove_network(args) -> int:
    conn = await connect()
    try:
        ok, msg = await _remove_network(conn, args.network_id)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_list_sources(args) -> int:
    conn = await connect()
    try:
        rows = await _fetch_data_sources(conn)
        if not rows:
            print("No data sources found.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'ID':<5} {'Source Code':<25} {'Provider':<20} {'Category':<15} {'Tier':<8} {'RPM'}")
            print("─" * 80)
            for r in rows:
                print(
                    f"{r['id']:<5} {r['source_code']:<25} {r['provider_name']:<20} "
                    f"{r['category']:<15} {r['tier_name']:<8} {r['rate_limit_rpm']}"
                )
    finally:
        await conn.close()
    return 0


async def cmd_list_mappings(args) -> int:
    conn = await connect()
    try:
        mappings = await _fetch_asset_mappings(conn, args.asset_id)
        if not mappings:
            print(f"No source mappings for asset {args.asset_id}.")
            return 0
        if args.json:
            print(json.dumps(mappings, default=str, indent=2))
        else:
            print(f"{'Source Code':<25} {'Identifier':<25} {'Symbol':<10} {'Provider'}")
            print("─" * 70)
            for m in mappings:
                print(
                    f"{m['source_code']:<25} {m['source_identifier']:<25} "
                    f"{m['source_symbol'] or '—':<10} {m['provider_name']}"
                )
    finally:
        await conn.close()
    return 0


async def cmd_pricing_pairs(args) -> int:
    conn = await connect()
    try:
        pairs = await _build_pricing_pairs(conn, exchange_source_code=args.exchange_source)
        if not pairs:
            print("No pricing pairs available. Allow-list some assets first.")
            return 0
        if args.json:
            print(json.dumps(pairs, default=str, indent=2))
        else:
            print(f"Pricing bot pairs ({len(pairs)} total):\n")
            print(f"  {'Symbol':<14} {'Base ID':>8} {'Quote ID':>9}")
            print(f"  {'─'*35}")
            for p in pairs:
                print(f"  {p['symbol']:<14} {p['base_asset_id']:>8} {p['quote_asset_id']:>9}")
            print(f"\nTo run pricing bot with these pairs:")
            pair_args = " ".join(f"--pair {p['pair']}" for p in pairs)
            print(f"  python -m bots.pricing_bot.main {pair_args}")
    finally:
        await conn.close()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  INTERACTIVE MODE
# ══════════════════════════════════════════════════════════════════════════════

async def interactive_list_assets(conn: asyncpg.Connection) -> None:
    header("Allow-Listed Assets")
    rows = await _fetch_assets(conn)
    if not rows:
        warn("No assets found.")
        return
    print(f"  {BOLD}{'ID':<5} {'Code':<8} {'Name':<20} {'Symbol':<8} {'Type':<8} {'Network':<10} Mappings{RESET}")
    print(f"  {DIM}{'─'*70}{RESET}")
    for r in rows:
        print(
            f"  {BOLD}{r['id']:<5}{RESET} {r['code']:<8} {r['name']:<20} {r['symbol']:<8} "
            f"{r['type']:<8} {r['network_code'] or '—':<10} {r['mapping_count']}"
        )


async def interactive_view_asset(conn: asyncpg.Connection) -> None:
    header("View Asset")
    sid = prompt_int("Asset ID")
    if sid is None:
        return
    r = await _fetch_asset_detail(conn, asset_id=sid)
    if not r:
        error(f"Asset {sid} not found.")
        return
    mappings = await _fetch_asset_mappings(conn, r["id"])

    print(f"\n  {BOLD}ID:{RESET}          {r['id']}")
    print(f"  {BOLD}Code:{RESET}        {r['code']}")
    print(f"  {BOLD}Name:{RESET}        {r['name']}")
    print(f"  {BOLD}Symbol:{RESET}      {r['symbol']}")
    print(f"  {BOLD}Type:{RESET}        {r['type']}")
    print(f"  {BOLD}Network:{RESET}     {r.get('network_code') or '—'}")
    print(f"  {BOLD}Created:{RESET}     {r['created_at'].strftime('%Y-%m-%d %H:%M')}")
    if mappings:
        print(f"\n  {BOLD}Source Mappings:{RESET}")
        for m in mappings:
            print(f"    {m['source_code']:<25} → {m['source_identifier']}  ({m['provider_name']})")


async def interactive_add_asset(conn: asyncpg.Connection) -> None:
    header("Add Asset (Allow-List)")
    query = prompt("Search CoinGecko coins (name/symbol)")
    if not query:
        return
    results = await _search_coins(conn, query, limit=10)
    if not results:
        warn(f"No coins matching '{query}'.")
        return
    print(f"\n  {BOLD}{'#':<4} {'CoinGecko ID':<30} {'Symbol':<10} Name{RESET}")
    for i, r in enumerate(results, 1):
        print(f"  {i:<4} {r['coingecko_id']:<30} {r['symbol']:<10} {r['name']}")
    idx = prompt_int("Select # to allow-list")
    if idx is None or idx < 1 or idx > len(results):
        return
    selected = results[idx - 1]

    cmc_id_str = prompt("CMC ID (optional, press Enter to skip)")
    cmc_id = int(cmc_id_str) if cmc_id_str.isdigit() else None

    if not prompt_bool(f"Allow-list '{selected['coingecko_id']}' ({selected['name']})?"):
        warn("Cancelled.")
        return

    ok, msg, _ = await _add_asset(conn, selected["coingecko_id"], cmc_id=cmc_id)
    if ok:
        success(msg)
    else:
        error(msg)


async def interactive_remove_asset(conn: asyncpg.Connection) -> None:
    header("Remove Asset")
    sid = prompt_int("Asset ID to remove")
    if sid is None:
        return
    r = await _fetch_asset_detail(conn, asset_id=sid)
    if not r:
        error(f"Asset {sid} not found.")
        return
    print(f"\n  {RED}This will soft-delete: {BOLD}{r['code']} — {r['name']}{RESET}")
    if not prompt_bool("Confirm remove?", default=False):
        warn("Cancelled.")
        return
    ok, msg = await _remove_asset(conn, sid)
    if ok:
        success(msg)
    else:
        error(msg)


async def interactive_list_networks(conn: asyncpg.Connection) -> None:
    header("Allow-Listed Networks")
    rows = await _fetch_networks(conn)
    if not rows:
        warn("No networks found.")
        return
    print(f"  {BOLD}{'ID':<5} {'Code':<12} {'Name':<25} Category{RESET}")
    print(f"  {DIM}{'─'*50}{RESET}")
    for r in rows:
        print(f"  {r['id']:<5} {r['code']:<12} {r['name']:<25} {r['category']}")


async def interactive_pricing_pairs(conn: asyncpg.Connection) -> None:
    header("Pricing Bot Pairs")
    pairs = await _build_pricing_pairs(conn)
    if not pairs:
        warn("No pairs available. Allow-list some assets first.")
        return
    print(f"  {BOLD}{'Symbol':<14} {'Base ID':>8} {'Quote ID':>9}{RESET}")
    print(f"  {DIM}{'─'*35}{RESET}")
    for p in pairs:
        print(f"  {p['symbol']:<14} {p['base_asset_id']:>8} {p['quote_asset_id']:>9}")
    print(f"\n  {DIM}Total: {len(pairs)} pairs{RESET}")


async def assets_menu_interactive(conn: asyncpg.Connection) -> None:
    while True:
        header("Manage Assets")
        choice = menu([
            "List allow-listed assets",
            "View asset details + mappings",
            "Add asset (allow-list from CoinGecko)",
            "Remove asset",
        ])
        if   choice == "0": break
        elif choice == "1": await interactive_list_assets(conn)
        elif choice == "2": await interactive_view_asset(conn)
        elif choice == "3": await interactive_add_asset(conn)
        elif choice == "4": await interactive_remove_asset(conn)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


async def networks_menu_interactive(conn: asyncpg.Connection) -> None:
    while True:
        header("Manage Networks")
        choice = menu([
            "List allow-listed networks",
            "Add network (allow-list from CoinGecko)",
            "Remove network",
        ])
        if   choice == "0": break
        elif choice == "1": await interactive_list_networks(conn)
        elif choice == "2":
            header("Add Network")
            coingecko_id = prompt("CoinGecko platform ID (e.g. ethereum)")
            if coingecko_id:
                ok, msg, _ = await _add_network(conn, coingecko_id)
                if ok:
                    success(msg)
                else:
                    error(msg)
        elif choice == "3":
            header("Remove Network")
            nid = prompt_int("Network ID to remove")
            if nid:
                ok, msg = await _remove_network(conn, nid)
                if ok:
                    success(msg)
                else:
                    error(msg)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


async def run_interactive() -> int:
    print(f"\n{BOLD}{CYAN}  ╔══════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}  ║     Asset Management CLI             ║{RESET}")
    print(f"{BOLD}{CYAN}  ╚══════════════════════════════════════╝{RESET}")

    try:
        conn = await connect()
    except Exception as exc:
        error(f"Cannot connect to DB: {exc}")
        return 1

    try:
        while True:
            header("Main Menu")
            choice = menu([
                "Manage Assets",
                "Manage Networks",
                "View Data Sources",
                "Show Pricing Bot Pairs",
            ])
            if   choice == "0": break
            elif choice == "1": await assets_menu_interactive(conn)
            elif choice == "2": await networks_menu_interactive(conn)
            elif choice == "3":
                header("Data Sources")
                rows = await _fetch_data_sources(conn)
                if not rows:
                    warn("No data sources found.")
                else:
                    print(f"  {BOLD}{'ID':<5} {'Source Code':<25} {'Provider':<20} {'Category':<15} {'Tier':<8} RPM{RESET}")
                    print(f"  {DIM}{'─'*80}{RESET}")
                    for r in rows:
                        print(
                            f"  {r['id']:<5} {r['source_code']:<25} {r['provider_name']:<20} "
                            f"{r['category']:<15} {r['tier_name']:<8} {r['rate_limit_rpm']}"
                        )
                input(f"\n  {DIM}Press Enter to continue...{RESET}")
            elif choice == "4":
                await interactive_pricing_pairs(conn)
                input(f"\n  {DIM}Press Enter to continue...{RESET}")
    finally:
        await conn.close()

    print(f"\n  {DIM}Bye.{RESET}\n")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  CLI PARSER
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manage allow-listed assets, networks, and source mappings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Non-interactive examples:
  %(prog)s list-assets
  %(prog)s list-assets --json
  %(prog)s view-asset --code btc
  %(prog)s view-asset --asset-id 5 --json
  %(prog)s add-asset --coingecko-id bitcoin
  %(prog)s add-asset --coingecko-id ethereum --cmc-id 1027
  %(prog)s add-asset --coingecko-id dogecoin --dry-run
  %(prog)s remove-asset --asset-id 5
  %(prog)s search-coins --query dog
  %(prog)s list-networks
  %(prog)s add-network --coingecko-id ethereum
  %(prog)s remove-network --network-id 3
  %(prog)s list-sources
  %(prog)s list-mappings --asset-id 5
  %(prog)s pricing-pairs
  %(prog)s pricing-pairs --json

Interactive mode (no arguments):
  %(prog)s
  %(prog)s interactive
        """,
    )
    sub = p.add_subparsers(dest="command")

    # interactive
    sub.add_parser("interactive", help="Launch interactive menu (default when no args)")

    # list-assets
    sp = sub.add_parser("list-assets", help="List all allow-listed assets")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # view-asset
    sp = sub.add_parser("view-asset", help="View asset details and source mappings")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--asset-id", type=int, help="Asset ID")
    g.add_argument("--code", type=str, help="Asset code (e.g. btc)")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # add-asset
    sp = sub.add_parser("add-asset", help="Allow-list an asset from CoinGecko")
    sp.add_argument("--coingecko-id", required=True, help="CoinGecko coin ID (e.g. bitcoin)")
    sp.add_argument("--cmc-id", type=int, default=None, help="CoinMarketCap ID (optional)")
    sp.add_argument("--code", default=None, help="Override internal code")
    sp.add_argument("--name", default=None, help="Override internal name")
    sp.add_argument("--dry-run", action="store_true", help="Preview without writing")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # remove-asset
    sp = sub.add_parser("remove-asset", help="Soft-delete an allow-listed asset")
    sp.add_argument("--asset-id", type=int, required=True, help="Asset ID")

    # search-coins
    sp = sub.add_parser("search-coins", help="Search CoinGecko coins universe")
    sp.add_argument("--query", required=True, help="Search term (name, symbol, or coingecko_id)")
    sp.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # list-networks
    sp = sub.add_parser("list-networks", help="List all allow-listed networks")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # add-network
    sp = sub.add_parser("add-network", help="Allow-list a network from CoinGecko")
    sp.add_argument("--coingecko-id", required=True, help="CoinGecko platform ID")
    sp.add_argument("--code", default=None, help="Override internal code")
    sp.add_argument("--name", default=None, help="Override internal name")
    sp.add_argument("--category", default="blockchain", choices=["blockchain", "legacy"])
    sp.add_argument("--dry-run", action="store_true", help="Preview without writing")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # remove-network
    sp = sub.add_parser("remove-network", help="Soft-delete an allow-listed network")
    sp.add_argument("--network-id", type=int, required=True, help="Network ID")

    # list-sources
    sp = sub.add_parser("list-sources", help="List all registered data sources")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # list-mappings
    sp = sub.add_parser("list-mappings", help="List source mappings for an asset")
    sp.add_argument("--asset-id", type=int, required=True, help="Asset ID")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # pricing-pairs
    sp = sub.add_parser("pricing-pairs", help="Show pricing bot pairs from allow-listed assets")
    sp.add_argument("--exchange-source", default=None, help="Filter by exchange source_code")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    return p


async def dispatch(args) -> int:
    cmd = args.command
    if cmd is None or cmd == "interactive":
        return await run_interactive()
    elif cmd == "list-assets":
        return await cmd_list_assets(args)
    elif cmd == "view-asset":
        return await cmd_view_asset(args)
    elif cmd == "add-asset":
        return await cmd_add_asset(args)
    elif cmd == "remove-asset":
        return await cmd_remove_asset(args)
    elif cmd == "search-coins":
        return await cmd_search_coins(args)
    elif cmd == "list-networks":
        return await cmd_list_networks(args)
    elif cmd == "add-network":
        return await cmd_add_network(args)
    elif cmd == "remove-network":
        return await cmd_remove_network(args)
    elif cmd == "list-sources":
        return await cmd_list_sources(args)
    elif cmd == "list-mappings":
        return await cmd_list_mappings(args)
    elif cmd == "pricing-pairs":
        return await cmd_pricing_pairs(args)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(dispatch(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
