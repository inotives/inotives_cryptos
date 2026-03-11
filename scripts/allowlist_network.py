"""
Allow-list a network for trading/tracking.

Looks up a platform in coingecko.raw_platforms (must be synced first), then
inserts it into base.networks and base.network_source_mappings so it becomes
visible to all pipelines and bots.

Usage:
    # Dry-run (preview only):
    uv run --env-file configs/envs/.env.local python scripts/allowlist_network.py \\
        --coingecko-id ethereum --dry-run

    # Allow-list Ethereum (category defaults to 'blockchain'):
    uv run --env-file configs/envs/.env.local python scripts/allowlist_network.py \\
        --coingecko-id ethereum

    # Override internal code / name:
    uv run --env-file configs/envs/.env.local python scripts/allowlist_network.py \\
        --coingecko-id binance-smart-chain --code BSC --name "BNB Smart Chain"

    # Legacy network (e.g. a stock exchange):
    uv run --env-file configs/envs/.env.local python scripts/allowlist_network.py \\
        --coingecko-id nasdaq --category legacy
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


async def run(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(settings.db_dsn)
    try:
        # ── 1. Look up platform in coingecko.raw_platforms ───────────────────
        platform = await conn.fetchrow(
            """
            SELECT coingecko_id, name, shortname, chain_identifier, native_coin_id
            FROM coingecko.raw_platforms
            WHERE coingecko_id = $1
            """,
            args.coingecko_id,
        )
        if not platform:
            print(
                f"ERROR: '{args.coingecko_id}' not found in coingecko.raw_platforms.\n"
                "Run the coingecko-platforms-weekly flow first to populate the table."
            )
            sys.exit(1)

        # Resolve final code / name / category (CLI overrides take precedence)
        code     = (args.code or platform["shortname"] or platform["coingecko_id"]).upper()
        name     = args.name or platform["name"]
        category = args.category

        print(f"\nNetwork to allow-list:")
        print(f"  coingecko_id     : {platform['coingecko_id']}")
        print(f"  code             : {code}")
        print(f"  name             : {name}")
        print(f"  category         : {category}")
        print(f"  chain_identifier : {platform['chain_identifier']}")
        print(f"  native_coin_id   : {platform['native_coin_id']}")
        print()

        if args.dry_run:
            print("DRY RUN — no changes written.")
            return

        # ── 2. Resolve CoinGecko source ID ───────────────────────────────────
        cg_source_id = await conn.fetchval(
            "SELECT id FROM base.data_sources WHERE source_code = $1",
            COINGECKO_SOURCE_CODE,
        )
        if not cg_source_id:
            print(f"ERROR: data source '{COINGECKO_SOURCE_CODE}' not found in base.data_sources.")
            sys.exit(1)

        # ── 3. Upsert into base.networks ─────────────────────────────────────
        network_id = await conn.fetchval(
            """
            INSERT INTO base.networks (code, name, category)
            VALUES ($1, $2, $3::base.network_category)
            ON CONFLICT (code) DO UPDATE SET
                name       = EXCLUDED.name,
                category   = EXCLUDED.category,
                updated_at = NOW()
            RETURNING id
            """,
            code, name, category,
        )
        print(f"✓ base.networks: id={network_id}  code={code}  name={name}  category={category}")

        # ── 4. Upsert CoinGecko source mapping ───────────────────────────────
        await conn.execute(
            """
            INSERT INTO base.network_source_mappings
                (network_id, source_id, source_identifier, source_name,
                 metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (network_id, source_id) DO UPDATE SET
                source_identifier = EXCLUDED.source_identifier,
                source_name       = EXCLUDED.source_name,
                metadata          = EXCLUDED.metadata,
                updated_at        = NOW()
            """,
            network_id,
            cg_source_id,
            platform["coingecko_id"],
            platform["name"],
            str({
                "chain_identifier": platform["chain_identifier"],
                "native_coin_id":   platform["native_coin_id"],
            }).replace("'", '"'),
        )
        print(f"✓ base.network_source_mappings: {COINGECKO_SOURCE_CODE} → '{platform['coingecko_id']}'")

        print(f"\nDone. '{code}' is now allow-listed.")

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Allow-list a CoinGecko platform into base.networks."
    )
    parser.add_argument(
        "--coingecko-id", required=True,
        help="CoinGecko platform ID, e.g. 'ethereum', 'binance-smart-chain'. "
             "Must exist in coingecko.raw_platforms.",
    )
    parser.add_argument(
        "--code", default=None,
        help="Override internal network code (default: CoinGecko shortname or id, uppercased).",
    )
    parser.add_argument(
        "--name", default=None,
        help="Override internal network name (default: CoinGecko name).",
    )
    parser.add_argument(
        "--category", default="blockchain", choices=["blockchain", "legacy"],
        help="Network category (default: blockchain).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be inserted without writing anything.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
