"""
Seed inotives_tradings.assets from a CSV file.

Usage:
    uv run --env-file configs/envs/.env.local python db/scripts/seed_assets_from_csv.py
    uv run --env-file configs/envs/.env.local python db/scripts/seed_assets_from_csv.py --csv db/seeds/assets_seed_B001.csv
    uv run --env-file configs/envs/.env.local python db/scripts/seed_assets_from_csv.py --dry-run

CSV columns:
    asset_code, asset_name, asset_symbol, asset_contract_address, asset_decimals,
    network, network-code, is_fee, is_origin, asset_type, asset_tag,
    cannonical_asset, backing_asset

Seeding runs in 3 phases:
    1. Insert origin/canonical assets  (cannonical_asset is empty)
    2. Insert non-canonical assets     (cannonical_asset is set)
    3. Update backing_asset_id         (backing_asset is set)
"""

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

from common.config import settings


DEFAULT_CSV = Path("db/seeds/assets_seed_B001.csv")


def get_type(row: dict) -> str:
    t   = row["asset_type"].strip()
    tag = row["asset_tag"].strip()
    if t in ("fiat", "equity"):
        return t
    if tag == "native":        return "native"
    if tag == "stablecoin":    return "stablecoin"
    if tag == "bridge-token":  return "bridge-token"
    if tag == "xstock":        return "xstock"
    return "token"


def parse_row(row: dict) -> dict:
    decimals_raw = row["asset_decimals"].strip()
    return {
        "code":             row["asset_code"].strip(),
        "name":             row["asset_name"].strip(),
        "symbol":           row["asset_symbol"].strip().upper(),
        "type":             get_type(row),
        "network_code":     row["network-code"].strip(),
        "contract_address": row["asset_contract_address"].strip() or None,
        "decimals":         int(decimals_raw) if decimals_raw else None,
        "is_fee_paying":    row["is_fee"].strip().upper() == "TRUE",
        "is_origin_asset":  row["is_origin"].strip().upper() == "TRUE",
        # treat self-referential canonical as origin
        "canonical_code":   (
            None if not row["cannonical_asset"].strip() or row["cannonical_asset"].strip() == row["asset_code"].strip()
            else row["cannonical_asset"].strip()
        ),
        "backing_code":     row["backing_asset"].strip() or None,
        "metadata":         {"tag": row["asset_tag"].strip() or None},
    }


async def seed(csv_path: Path, dry_run: bool) -> None:
    import asyncpg

    # Load and parse CSV
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = [parse_row(r) for r in csv.DictReader(f)]

    origin_rows     = [r for r in rows if not r["canonical_code"]]
    non_origin_rows = [r for r in rows if r["canonical_code"]]
    backing_rows    = [r for r in rows if r["backing_code"]]

    print(f"CSV rows loaded        : {len(rows)}")
    print(f"  Origin/canonical     : {len(origin_rows)}")
    print(f"  Non-canonical        : {len(non_origin_rows)}")
    print(f"  With backing_asset   : {len(backing_rows)}")

    if dry_run:
        print("\n-- DRY RUN: no changes written --")
        print("\n[Phase 1] Origin assets:")
        for r in origin_rows[:5]:
            print(f"  {r['code']:<40} type={r['type']:<12} network={r['network_code']}")
        print(f"  ... and {len(origin_rows) - 5} more")
        print("\n[Phase 2] Non-canonical assets (sample):")
        for r in non_origin_rows[:5]:
            print(f"  {r['code']:<40} canonical={r['canonical_code']}")
        return

    conn = await asyncpg.connect(settings.db_dsn)

    try:
        # Pre-load network code → id map
        network_rows = await conn.fetch("SELECT id, code FROM inotives_tradings.networks")
        network_map  = {r["code"]: r["id"] for r in network_rows}

        # ── Phase 1: Insert origin/canonical assets ───────────────────────
        print("\n[Phase 1] Seeding origin/canonical assets...")
        inserted = updated = skipped = 0

        for r in origin_rows:
            network_id = network_map.get(r["network_code"])
            if not network_id:
                # Equity/non-blockchain assets have no network — insert with NULL
                if r["type"] not in ("equity", "fiat") or r["network_code"]:
                    print(f"  SKIP  network not found: {r['network_code']!r} (asset: {r['code']})")
                    skipped += 1
                    continue

            row = await conn.fetchrow(
                """
                INSERT INTO inotives_tradings.assets
                    (code, name, symbol, type, network_id, contract_address,
                     decimals, is_fee_paying, is_origin_asset, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (code) DO UPDATE SET
                    name             = EXCLUDED.name,
                    symbol           = EXCLUDED.symbol,
                    type             = EXCLUDED.type,
                    network_id       = EXCLUDED.network_id,
                    contract_address = EXCLUDED.contract_address,
                    decimals         = EXCLUDED.decimals,
                    is_fee_paying    = EXCLUDED.is_fee_paying,
                    is_origin_asset  = EXCLUDED.is_origin_asset,
                    metadata         = EXCLUDED.metadata
                RETURNING id, (xmax = 0) AS is_insert
                """,
                r["code"], r["name"], r["symbol"], r["type"],
                network_id, r["contract_address"], r["decimals"],
                r["is_fee_paying"], r["is_origin_asset"],
                json.dumps({k: v for k, v in r["metadata"].items() if v}),
            )
            if row["is_insert"]:
                inserted += 1
            else:
                updated += 1

        print(f"  Done — {inserted} inserted, {updated} updated, {skipped} skipped.")

        # Reload code → id map after phase 1
        asset_rows = await conn.fetch("SELECT id, code FROM inotives_tradings.assets")
        asset_map  = {r["code"]: r["id"] for r in asset_rows}

        # ── Phase 2: Insert non-canonical assets ──────────────────────────
        print("\n[Phase 2] Seeding non-canonical assets...")
        inserted = updated = skipped = 0

        for r in non_origin_rows:
            network_id    = network_map.get(r["network_code"])
            canonical_id  = asset_map.get(r["canonical_code"])

            if not network_id:
                print(f"  SKIP  network not found: {r['network_code']!r} (asset: {r['code']})")
                skipped += 1
                continue

            if not canonical_id:
                print(f"  SKIP  canonical not found: {r['canonical_code']!r} (asset: {r['code']})")
                skipped += 1
                continue

            row = await conn.fetchrow(
                """
                INSERT INTO inotives_tradings.assets
                    (code, name, symbol, type, network_id, contract_address,
                     decimals, is_fee_paying, is_origin_asset, canonical_asset_id, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (code) DO UPDATE SET
                    name               = EXCLUDED.name,
                    symbol             = EXCLUDED.symbol,
                    type               = EXCLUDED.type,
                    network_id         = EXCLUDED.network_id,
                    contract_address   = EXCLUDED.contract_address,
                    decimals           = EXCLUDED.decimals,
                    is_fee_paying      = EXCLUDED.is_fee_paying,
                    is_origin_asset    = EXCLUDED.is_origin_asset,
                    canonical_asset_id = EXCLUDED.canonical_asset_id,
                    metadata           = EXCLUDED.metadata
                RETURNING id, (xmax = 0) AS is_insert
                """,
                r["code"], r["name"], r["symbol"], r["type"],
                network_id, r["contract_address"], r["decimals"],
                r["is_fee_paying"], r["is_origin_asset"], canonical_id,
                json.dumps({k: v for k, v in r["metadata"].items() if v}),
            )
            if row["is_insert"]:
                inserted += 1
            else:
                updated += 1

        print(f"  Done — {inserted} inserted, {updated} updated, {skipped} skipped.")

        # Reload asset map after phase 2
        asset_rows = await conn.fetch("SELECT id, code FROM inotives_tradings.assets")
        asset_map  = {r["code"]: r["id"] for r in asset_rows}

        # ── Phase 3: Update backing_asset_id ─────────────────────────────
        print("\n[Phase 3] Updating backing_asset_id...")
        updated = skipped = 0

        for r in backing_rows:
            asset_id   = asset_map.get(r["code"])
            backing_id = asset_map.get(r["backing_code"])

            if not asset_id or not backing_id:
                print(f"  SKIP  {r['code']} -> {r['backing_code']} (one not found)")
                skipped += 1
                continue

            await conn.execute(
                "UPDATE inotives_tradings.assets SET backing_asset_id = $1 WHERE id = $2",
                backing_id, asset_id,
            )
            updated += 1

        print(f"  Done — {updated} updated, {skipped} skipped.")

    finally:
        await conn.close()

    print("\nSeeding complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed inotives_tradings.assets from CSV.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}")
        sys.exit(1)

    asyncio.run(seed(args.csv, args.dry_run))


if __name__ == "__main__":
    main()
