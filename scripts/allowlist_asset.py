"""
Allow-list an asset for trading/tracking.

Looks up a coin in coingecko.raw_coins (must be synced first), then inserts
it into base.assets and base.asset_source_mappings so it becomes visible to
all pipelines and bots.

Usage:
    # Dry-run (preview only):
    uv run --env-file configs/envs/.env.local python scripts/allowlist_asset.py \\
        --coingecko-id bitcoin --dry-run

    # Allow-list BTC with CoinGecko only:
    uv run --env-file configs/envs/.env.local python scripts/allowlist_asset.py \\
        --coingecko-id bitcoin

    # Allow-list with CMC ID too:
    uv run --env-file configs/envs/.env.local python scripts/allowlist_asset.py \\
        --coingecko-id ethereum --cmc-id 1027

    # Override the internal code/name (defaults come from CoinGecko):
    uv run --env-file configs/envs/.env.local python scripts/allowlist_asset.py \\
        --coingecko-id bitcoin --code BTC --name Bitcoin
"""

import argparse
import asyncio
import sys

import asyncpg
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="configs/envs/.env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    @property
    def db_dsn(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"


settings = Settings()

COINGECKO_SOURCE_CODE = "api:coingecko"
CMC_SOURCE_CODE       = "api:coinmarketcap"


async def run(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(settings.db_dsn)
    try:
        # ── 1. Look up coin in coingecko.raw_coins ────────────────────────────
        coin = await conn.fetchrow(
            "SELECT coingecko_id, symbol, name FROM coingecko.raw_coins WHERE coingecko_id = $1",
            args.coingecko_id,
        )
        if not coin:
            print(
                f"ERROR: '{args.coingecko_id}' not found in coingecko.raw_coins.\n"
                "Run the coingecko-coins-list-daily flow first to populate the table."
            )
            sys.exit(1)

        # Resolve final code / name (CLI overrides take precedence)
        code = (args.code or coin["symbol"]).upper()
        name = args.name or coin["name"]

        print(f"\nAsset to allow-list:")
        print(f"  coingecko_id : {coin['coingecko_id']}")
        print(f"  code         : {code}")
        print(f"  name         : {name}")
        if args.cmc_id:
            print(f"  cmc_id       : {args.cmc_id}")
        print()

        if args.dry_run:
            print("DRY RUN — no changes written.")
            return

        # ── 2. Resolve source IDs ─────────────────────────────────────────────
        cg_source_id = await conn.fetchval(
            "SELECT id FROM base.data_sources WHERE source_code = $1",
            COINGECKO_SOURCE_CODE,
        )
        if not cg_source_id:
            print(f"ERROR: data source '{COINGECKO_SOURCE_CODE}' not found in base.data_sources.")
            sys.exit(1)

        cmc_source_id = None
        if args.cmc_id:
            cmc_source_id = await conn.fetchval(
                "SELECT id FROM base.data_sources WHERE source_code = $1",
                CMC_SOURCE_CODE,
            )
            if not cmc_source_id:
                print(f"WARNING: data source '{CMC_SOURCE_CODE}' not found — CMC mapping skipped.")

        # ── 3. Upsert into base.assets ────────────────────────────────────────
        asset_id = await conn.fetchval(
            """
            INSERT INTO base.assets (code, name, symbol, type, is_origin_asset)
            VALUES ($1, $2, $3, 'crypto', true)
            ON CONFLICT (code) DO UPDATE SET
                name       = EXCLUDED.name,
                symbol     = EXCLUDED.symbol,
                updated_at = NOW()
            RETURNING id
            """,
            code, name, code,
        )
        print(f"✓ base.assets: id={asset_id}  code={code}  name={name}")

        # ── 4. Upsert CoinGecko source mapping ───────────────────────────────
        await conn.execute(
            """
            INSERT INTO base.asset_source_mappings
                (asset_id, source_id, source_identifier, source_symbol, source_name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (asset_id, source_id) DO UPDATE SET
                source_identifier = EXCLUDED.source_identifier,
                source_symbol     = EXCLUDED.source_symbol,
                source_name       = EXCLUDED.source_name,
                updated_at        = NOW()
            """,
            asset_id, cg_source_id,
            coin["coingecko_id"], coin["symbol"], coin["name"],
        )
        print(f"✓ base.asset_source_mappings: {COINGECKO_SOURCE_CODE} → '{coin['coingecko_id']}'")

        # ── 5. Optionally upsert CMC source mapping ───────────────────────────
        if args.cmc_id and cmc_source_id:
            await conn.execute(
                """
                INSERT INTO base.asset_source_mappings
                    (asset_id, source_id, source_identifier, source_symbol, source_name)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (asset_id, source_id) DO UPDATE SET
                    source_identifier = EXCLUDED.source_identifier,
                    source_symbol     = EXCLUDED.source_symbol,
                    source_name       = EXCLUDED.source_name,
                    updated_at        = NOW()
                """,
                asset_id, cmc_source_id,
                str(args.cmc_id), coin["symbol"], coin["name"],
            )
            print(f"✓ base.asset_source_mappings: {CMC_SOURCE_CODE} → '{args.cmc_id}'")

        print(f"\nDone. '{code}' is now allow-listed and will be picked up by all pipelines.")

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Allow-list a CoinGecko coin into base.assets for trading/tracking."
    )
    parser.add_argument(
        "--coingecko-id", required=True,
        help="CoinGecko coin ID, e.g. 'bitcoin', 'ethereum'. Must exist in coingecko.raw_coins.",
    )
    parser.add_argument(
        "--cmc-id", type=int, default=None,
        help="CoinMarketCap numeric ID (optional). Adds a second source mapping.",
    )
    parser.add_argument(
        "--code", default=None,
        help="Override internal asset code (default: CoinGecko symbol uppercased).",
    )
    parser.add_argument(
        "--name", default=None,
        help="Override internal asset name (default: CoinGecko name).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be inserted without writing anything.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
