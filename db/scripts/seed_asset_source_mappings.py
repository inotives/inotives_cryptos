"""
Seed inotives_tradings.asset_source_mappings from CSV.

Links internal asset codes to their identifiers on external data sources
(CoinGecko slugs, CoinMarketCap numeric IDs, etc.).

Usage:
    uv run --env-file configs/envs/.env.local python db/scripts/seed_asset_source_mappings.py
    uv run --env-file configs/envs/.env.local python db/scripts/seed_asset_source_mappings.py --dry-run

CSV columns expected:
    asset_code, source_code, source_identifier, source_symbol, source_name
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from common.config import settings


DEFAULT_CSV = Path("db/seeds/asset_source_mappings_seeds.csv")


def parse_row(row: dict) -> tuple | None:
    asset_code        = row.get("asset_code", "").strip()
    source_code       = row.get("source_code", "").strip()
    source_identifier = row.get("source_identifier", "").strip()
    source_symbol     = row.get("source_symbol", "").strip() or None
    source_name       = row.get("source_name", "").strip() or None

    if not asset_code or not source_code or not source_identifier:
        return None
    return asset_code, source_code, source_identifier, source_symbol, source_name


async def seed(csv_path: Path, dry_run: bool) -> None:
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            parsed = parse_row(raw)
            if parsed:
                rows.append(parsed)

    print(f"Loaded {len(rows)} rows from {csv_path}")

    if dry_run:
        print("\n-- DRY RUN: no changes written --")
        print(f"{'Asset':<8} {'Source':<22} {'Identifier':<20} {'Symbol':<8} Name")
        print("-" * 75)
        for asset_code, source_code, source_identifier, source_symbol, source_name in rows:
            print(f"  {asset_code:<8} {source_code:<22} {source_identifier:<20} {str(source_symbol):<8} {source_name}")
        return

    import asyncpg
    conn = await asyncpg.connect(settings.db_dsn)
    inserted = updated = skipped = 0

    try:
        for asset_code, source_code, source_identifier, source_symbol, source_name in rows:
            asset_id = await conn.fetchval(
                "SELECT id FROM inotives_tradings.assets WHERE code = $1", asset_code
            )
            if not asset_id:
                print(f"  SKIP  {asset_code!r}: not found in inotives_tradings.assets")
                skipped += 1
                continue

            source_id = await conn.fetchval(
                "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1", source_code
            )
            if not source_id:
                print(f"  SKIP  {source_code!r}: not found in inotives_tradings.data_sources")
                skipped += 1
                continue

            existing = await conn.fetchval(
                "SELECT id FROM inotives_tradings.asset_source_mappings WHERE asset_id=$1 AND source_id=$2",
                asset_id, source_id,
            )
            await conn.execute(
                """
                INSERT INTO inotives_tradings.asset_source_mappings
                    (asset_id, source_id, source_identifier, source_symbol, source_name)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (asset_id, source_id) DO UPDATE SET
                    source_identifier = EXCLUDED.source_identifier,
                    source_symbol     = EXCLUDED.source_symbol,
                    source_name       = EXCLUDED.source_name
                """,
                asset_id, source_id, source_identifier, source_symbol, source_name,
            )
            if existing:
                updated += 1
                print(f"  UPDATE  {asset_code} → {source_code} ({source_identifier})")
            else:
                inserted += 1
                print(f"  INSERT  {asset_code} → {source_code} ({source_identifier})")

    finally:
        await conn.close()

    print(f"\nDone. {inserted + updated + skipped} rows — {inserted} inserted, {updated} updated, {skipped} skipped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed inotives_tradings.asset_source_mappings from CSV.")
    parser.add_argument("--csv",     type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV file not found: {args.csv}")
        sys.exit(1)

    asyncio.run(seed(args.csv, args.dry_run))


if __name__ == "__main__":
    main()
