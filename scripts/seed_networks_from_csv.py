"""
Seed base.networks from a CSV file.

Usage:
    uv run --env-file configs/envs/.env.local python scripts/seed_networks_from_csv.py
    uv run --env-file configs/envs/.env.local python scripts/seed_networks_from_csv.py --csv db/seeds/network_seeds.csv
    uv run --env-file configs/envs/.env.local python scripts/seed_networks_from_csv.py --dry-run

CSV columns expected:
    network_code, network_name, network_description, explorer,
    protocol, native_asset_code, chain_id, network_class, network_category
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path


DEFAULT_CSV = Path("db/seeds/network_seeds.csv")


def get_dsn() -> str:
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5435")
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    name = os.environ.get("DB_NAME")

    if not all([user, password, name]):
        print("ERROR: DB_USER, DB_PASSWORD, and DB_NAME must be set in the environment.")
        sys.exit(1)

    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def parse_row(row: dict) -> tuple[str, str, str, dict] | None:
    """Parse a CSV row into (code, name, category, metadata). Returns None to skip."""
    code = row.get("network_code", "").strip()
    name = row.get("network_name", "").strip()
    category = row.get("network_category", "").strip().lower()

    if not code or not name:
        return None

    if category not in ("legacy", "blockchain"):
        print(f"  SKIP  {code!r}: invalid category {category!r}")
        return None

    # Pack extra fields into metadata — omit empty values
    metadata = {}
    for key, col in (
        ("description",       "network_description"),
        ("explorer",          "explorer"),
        ("protocol",          "protocol"),
        ("native_asset_code", "native_asset_code"),
        ("chain_id",          "chain_id"),
        ("network_class",     "network_class"),
    ):
        val = row.get(col, "").strip()
        if val:
            metadata[key] = val

    return code, name, category, metadata


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
        for code, name, category, metadata in rows:
            print(f"  {code:<40} {category:<12} {name}")
        return

    import asyncpg
    conn = await asyncpg.connect(get_dsn())
    inserted = updated = skipped = 0

    try:
        for code, name, category, metadata in rows:
            result = await conn.execute(
                """
                INSERT INTO base.networks (code, name, category, metadata)
                VALUES ($1, $2, $3::base.network_category, $4::jsonb)
                ON CONFLICT (code) DO UPDATE
                    SET name     = EXCLUDED.name,
                        category = EXCLUDED.category,
                        metadata = EXCLUDED.metadata
                """,
                code, name, category, json.dumps(metadata),
            )
            # asyncpg returns "INSERT 0 1" or "UPDATE 1"
            if result.startswith("INSERT"):
                inserted += 1
                print(f"  INSERT  {code}")
            else:
                updated += 1
                print(f"  UPDATE  {code}")

    finally:
        await conn.close()

    total = inserted + updated + skipped
    print(f"\nDone. {total} rows processed — {inserted} inserted, {updated} updated, {skipped} skipped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed base.networks from CSV.")
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
