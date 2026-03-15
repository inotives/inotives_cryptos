"""
Seed inotives_tradings.asset_metrics_1d from a CoinMarketCap OHLCV CSV export.

Asset code is inferred from the first segment of the filename.
Data source must be provided via --source (or inferred if a known provider
keyword is present in the filename for backwards compatibility).

Usage:
    # Source inferred from filename (legacy filenames with provider keyword):
    uv run --env-file configs/envs/.env.local python db/scripts/seed_metrics_1d_from_csv.py \
        --csv db/seeds/btc_historical_data_coinmarketcap_20260101-20260309.csv

    # Source explicit (new filenames without provider keyword):
    uv run --env-file configs/envs/.env.local python db/scripts/seed_metrics_1d_from_csv.py \
        --csv db/seeds/btc_historical_data_20260309.csv --source api:coinmarketcap

    # Dry-run preview:
    uv run --env-file configs/envs/.env.local python db/scripts/seed_metrics_1d_from_csv.py \
        --csv <path> --source api:coinmarketcap --dry-run

CSV format (semicolon-delimited, CoinMarketCap daily export):
    timeOpen, timeClose, timeHigh, timeLow, name (CMC ID),
    open, high, low, close, volume, marketCap, circulatingSupply, timestamp
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.config import settings


# Maps provider keywords found in filename → source_code prefix
PROVIDER_MAP = {
    "coinmarketcap": "api:coinmarketcap",
    "coingecko":     "api:coingecko",
    "binance":       "exchange:binance",
    "cryptocom":     "exchange:cryptocom",
    "kraken":        "exchange:kraken",
    "coinbase":      "exchange:coinbase",
}


def infer_from_filename(csv_path: Path) -> tuple[str, str | None]:
    """
    Infer (asset_code, source_code) from the filename.

    Asset code is always the first underscore-separated segment.
    Source code is inferred only if a known provider keyword is present
    in the filename (backwards compatibility). Returns None for source_code
    if no provider keyword is found — caller must supply --source explicitly.

    e.g. btc_historical_data_coinmarketcap_20260101-20260309.csv → ('btc', 'api:coinmarketcap')
         btc_historical_data_20260309.csv                        → ('btc', None)
    """
    parts = csv_path.stem.lower().split("_")

    asset_code  = parts[0]
    source_code = None
    for part in parts:
        if part in PROVIDER_MAP:
            source_code = PROVIDER_MAP[part]
            break

    return asset_code, source_code


def parse_ts(val: str) -> datetime | None:
    val = val.strip().strip('"')
    if not val:
        return None
    return datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_decimal(val: str) -> float | None:
    val = val.strip().strip('"')
    try:
        return float(val) if val else None
    except ValueError:
        return None


def load_csv(csv_path: Path) -> list[dict]:
    """Parse CSV into a list of row dicts, sorted ascending by date."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:  # utf-8-sig strips BOM
        reader = csv.DictReader(f, delimiter=";")
        for raw in reader:
            time_open = parse_ts(raw.get("timeOpen", ""))
            time_high = parse_ts(raw.get("timeHigh", ""))
            time_low  = parse_ts(raw.get("timeLow", ""))

            open_p  = parse_decimal(raw.get("open", ""))
            high_p  = parse_decimal(raw.get("high", ""))
            low_p   = parse_decimal(raw.get("low", ""))
            close_p = parse_decimal(raw.get("close", ""))
            volume  = parse_decimal(raw.get("volume", ""))
            mktcap  = parse_decimal(raw.get("marketCap", ""))
            supply  = parse_decimal(raw.get("circulatingSupply", ""))

            if not time_open or close_p is None:
                continue

            rows.append({
                "metric_date":        time_open.date(),
                "high_at":            time_high,
                "low_at":             time_low,
                "open_price":         open_p,
                "high_price":         high_p,
                "low_price":          low_p,
                "close_price":        close_p,
                "volume_usd":         volume,
                "market_cap_usd":     mktcap,
                "circulating_supply": supply,
                "metadata":           {},
            })

    # Sort oldest → newest so price_change_pct can be computed sequentially
    rows.sort(key=lambda r: r["metric_date"])

    # Compute price_change_pct vs previous day's close
    for i, row in enumerate(rows):
        prev_close = rows[i - 1]["close_price"] if i > 0 else None
        if prev_close and prev_close != 0:
            row["price_change_pct"] = round(
                (row["close_price"] - prev_close) / prev_close * 100, 6
            )
        else:
            row["price_change_pct"] = None

    return rows


async def seed(csv_path: Path, asset_code: str, source_code: str, dry_run: bool) -> None:
    rows = load_csv(csv_path)
    print(f"Loaded    : {len(rows)} rows from {csv_path.name}")
    print(f"Asset     : {asset_code}")
    print(f"Source    : {source_code}")
    print(f"Date range: {rows[0]['metric_date']} → {rows[-1]['metric_date']}\n")

    if dry_run:
        print("-- DRY RUN: no changes written --\n")
        print(f"{'Date':<12} {'Open':>14} {'High':>14} {'Low':>14} {'Close':>14} {'Volume USD':>20} {'Chg%':>8}")
        print("-" * 102)
        for r in rows:
            chg = f"{r['price_change_pct']:+.4f}" if r["price_change_pct"] is not None else "-"
            print(
                f"{str(r['metric_date']):<12}"
                f" {r['open_price']:>14.4f}"
                f" {r['high_price']:>14.4f}"
                f" {r['low_price']:>14.4f}"
                f" {r['close_price']:>14.4f}"
                f" {r['volume_usd']:>20.2f}"
                f" {chg:>8}"
            )
        return

    import asyncpg, json

    conn = await asyncpg.connect(settings.db_dsn)
    try:
        asset_row = await conn.fetchrow(
            "SELECT id FROM inotives_tradings.assets WHERE LOWER(code) = LOWER($1)", asset_code
        )
        if not asset_row:
            print(f"ERROR: asset '{asset_code}' not found in inotives_tradings.assets.")
            sys.exit(1)
        asset_id = asset_row["id"]

        source_row = await conn.fetchrow(
            "SELECT id FROM inotives_tradings.data_sources WHERE source_code = $1", source_code
        )
        if not source_row:
            print(f"ERROR: source '{source_code}' not found in inotives_tradings.data_sources.")
            sys.exit(1)
        source_id = source_row["id"]

        print(f"Resolved asset_id={asset_id}, source_id={source_id}\n")

        inserted = updated = 0
        for r in rows:
            existing = await conn.fetchval(
                """SELECT id FROM inotives_tradings.asset_metrics_1d
                   WHERE asset_id=$1 AND source_id=$2 AND metric_date=$3""",
                asset_id, source_id, r["metric_date"],
            )
            await conn.execute(
                """
                INSERT INTO inotives_tradings.asset_metrics_1d (
                    asset_id, source_id, metric_date,
                    open_price, high_price, low_price, close_price,
                    volume_usd, high_at, low_at,
                    price_change_pct, market_cap_usd, circulating_supply,
                    is_final, metadata
                ) VALUES (
                    $1, $2, $3,
                    $4, $5, $6, $7,
                    $8, $9, $10,
                    $11, $12, $13,
                    true, $14::jsonb
                )
                ON CONFLICT (asset_id, metric_date, source_id) DO UPDATE SET
                    open_price         = EXCLUDED.open_price,
                    high_price         = EXCLUDED.high_price,
                    low_price          = EXCLUDED.low_price,
                    close_price        = EXCLUDED.close_price,
                    volume_usd         = EXCLUDED.volume_usd,
                    high_at            = EXCLUDED.high_at,
                    low_at             = EXCLUDED.low_at,
                    price_change_pct   = EXCLUDED.price_change_pct,
                    market_cap_usd     = EXCLUDED.market_cap_usd,
                    circulating_supply = EXCLUDED.circulating_supply,
                    is_final           = EXCLUDED.is_final,
                    metadata           = EXCLUDED.metadata
                """,
                asset_id, source_id, r["metric_date"],
                r["open_price"], r["high_price"], r["low_price"], r["close_price"],
                r["volume_usd"], r["high_at"], r["low_at"],
                r["price_change_pct"], r["market_cap_usd"], r["circulating_supply"],
                json.dumps(r["metadata"]),
            )
            if existing:
                updated += 1
            else:
                inserted += 1

        print(f"Done. {inserted + updated} rows — {inserted} inserted, {updated} updated.")

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed inotives_tradings.asset_metrics_1d from a CMC OHLCV CSV. "
                    "Asset and source are inferred from the filename by default."
    )
    parser.add_argument("--csv",     type=Path, required=True, help="Path to the CSV file")
    parser.add_argument("--asset",   type=str,  default=None,  help="Override asset code (default: inferred from filename)")
    parser.add_argument("--source",  type=str,  default=None,  help="Override source code (default: inferred from filename)")
    parser.add_argument("--dry-run", action="store_true",      help="Print rows without writing to DB")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV file not found: {args.csv}")
        sys.exit(1)

    inferred_asset, inferred_source = infer_from_filename(args.csv)
    asset_code  = args.asset  or inferred_asset
    source_code = args.source or inferred_source

    if not source_code:
        known = ", ".join(PROVIDER_MAP.keys())
        print(f"ERROR: Could not infer source from filename '{args.csv.name}'.")
        print(f"       Pass --source explicitly, e.g. --source api:coinmarketcap")
        print(f"       Known provider keywords for auto-inference: {known}")
        sys.exit(1)

    asyncio.run(seed(args.csv, asset_code, source_code, args.dry_run))


if __name__ == "__main__":
    main()
