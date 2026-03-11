"""
Seed base.data_sources from a CSV file via base.upsert_data_source().

Usage:
    uv run --env-file configs/envs/.env.local python scripts/seed_data_sources_from_csv.py
    uv run --env-file configs/envs/.env.local python scripts/seed_data_sources_from_csv.py --csv db/seeds/data_sources_seeds.csv
    uv run --env-file configs/envs/.env.local python scripts/seed_data_sources_from_csv.py --dry-run

CSV columns expected:
    source_code, provider_name, category, tier_name, rate_limit_rpm, site_url
"""

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path


DEFAULT_CSV = Path("db/seeds/data_sources_seeds.csv")

VALID_CATEGORIES = {"MARKET_DATA", "CUSTODY", "ONCHAIN", "EXECUTION"}


def get_dsn() -> str:
    host     = os.environ.get("DB_HOST", "localhost")
    port     = os.environ.get("DB_PORT", "5435")
    user     = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    name     = os.environ.get("DB_NAME")

    if not all([user, password, name]):
        print("ERROR: DB_USER, DB_PASSWORD, and DB_NAME must be set in the environment.")
        sys.exit(1)

    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def parse_row(row: dict) -> tuple | None:
    """Parse and validate a CSV row. Returns None to skip."""
    source_code   = row.get("source_code", "").strip()
    provider_name = row.get("provider_name", "").strip()
    category      = row.get("category", "").strip().upper()

    if not source_code or not provider_name:
        return None

    if category not in VALID_CATEGORIES:
        print(f"  SKIP  {source_code!r}: invalid category {category!r}")
        return None

    tier_name      = row.get("tier_name", "free").strip() or "free"
    site_url       = row.get("site_url", "").strip() or None

    try:
        rate_limit_rpm = int(row.get("rate_limit_rpm", "30").strip() or "30")
    except ValueError:
        rate_limit_rpm = 30

    return source_code, provider_name, category, tier_name, rate_limit_rpm, site_url


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
        for source_code, provider_name, category, tier_name, rate_limit_rpm, site_url in rows:
            print(f"  {source_code:<35} {category:<15} {provider_name}")
        return

    import asyncpg
    conn = await asyncpg.connect(get_dsn())
    inserted = updated = 0

    try:
        for source_code, provider_name, category, tier_name, rate_limit_rpm, site_url in rows:
            exists = await conn.fetchval(
                "SELECT id FROM base.data_sources WHERE source_code = $1", source_code
            )
            await conn.fetchval(
                "SELECT base.upsert_data_source($1, $2, $3, $4, $5, $6)",
                source_code, provider_name, category, tier_name, rate_limit_rpm, site_url,
            )
            if exists:
                updated += 1
                print(f"  UPDATE  {source_code}")
            else:
                inserted += 1
                print(f"  INSERT  {source_code}")

    finally:
        await conn.close()

    print(f"\nDone. {inserted + updated} rows processed — {inserted} inserted, {updated} updated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed base.data_sources from CSV.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to the CSV file (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print rows without writing to the database.",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV file not found: {args.csv}")
        sys.exit(1)

    asyncio.run(seed(args.csv, args.dry_run))


if __name__ == "__main__":
    main()
