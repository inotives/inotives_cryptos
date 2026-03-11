ENV ?= local
ENV_PATH = configs/envs/.env.$(ENV)

# Load variables for shell commands if needed
include $(ENV_PATH)
export $(shell sed 's/=.*//' $(ENV_PATH))

# Helper for dbmate using uvx (ephemeral binary execution)

DB_URL_STR = postgres://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)?sslmode=disable

DBMATE = uv run --env-file $(ENV_PATH) uvx --from dbmate-bin dbmate --url "$(DB_URL_STR)"


.PHONY: init services-up services-down migrate-up migrate-down migrate-status \
        seed-data-sources seed-data-sources-dry \
        seed-metrics-1d seed-metrics-1d-dry \
        sync-coingecko-platforms sync-coingecko-coins \
        allowlist-asset allowlist-asset-dry \
        allowlist-network allowlist-network-dry \
        bootstrap \
        prefect-deploy prefect-ui \
        setup-paper-trading \
        pricing-bot trader-bot \
        manage-trading \
        grafana-ui

# 1. Setup the workspace
# initial setup when cloning the repo
init: 
	@echo "🚀 Bootstrapping inotives_data..."
	python3 -m venv .venv
	./.venv/bin/pip install uv
	./.venv/bin/uv sync
	@echo "✅ Setup complete. Virtual environment is ready."


# 2. Docker Lifecycle
services-up:
	docker compose --env-file $(ENV_PATH) up -d

services-down:
	docker compose --env-file $(ENV_PATH) down

# 3. DB Migrations
migrate-new:
	$(DBMATE) new $(name)

migrate-up:
	$(DBMATE) up

migrate-down:
	$(DBMATE) rollback

migrate-status:
	$(DBMATE) status

# 4. Bootstrap — initial reference data setup for a fresh environment
# Runs in sequence: seeds data sources, syncs CoinGecko reference tables,
# then allow-lists a default set of networks (ETH, BTC, SOL) and assets (BTC, ETH, SOL).
#
# NOTE: The default networks and assets (ETH, BTC, SOL) are a recommended starting point.
# To customise, edit this target or run allowlist-network / allowlist-asset individually:
#   make allowlist-network coingecko_id=binance-smart-chain
#   make allowlist-asset   coingecko_id=solana
# To remove a default, soft-delete the row directly in base.networks or base.assets.
bootstrap:
	@echo "--- [1/5] Seeding data sources ---"
	$(MAKE) seed-data-sources
	@echo "--- [2/5] Syncing CoinGecko platforms ---"
	$(MAKE) sync-coingecko-platforms
	@echo "--- [3/5] Syncing CoinGecko coins list ---"
	$(MAKE) sync-coingecko-coins
	@echo "--- [4/5] Allow-listing default networks (ETH, BTC, SOL) ---"
	$(MAKE) allowlist-network coingecko_id=ethereum
	$(MAKE) allowlist-network coingecko_id=bitcoin
	$(MAKE) allowlist-network coingecko_id=solana
	@echo "--- [5/5] Allow-listing default assets (BTC, ETH, SOL) ---"
	$(MAKE) allowlist-asset coingecko_id=bitcoin
	$(MAKE) allowlist-asset coingecko_id=ethereum
	$(MAKE) allowlist-asset coingecko_id=solana
	@echo "--- Bootstrap complete. Run 'make prefect-deploy' to register pipelines. ---"

# Run CoinGecko reference sync flows directly (bypasses Prefect scheduler).
# Use these for initial setup or manual refresh outside the scheduled runs.
sync-coingecko-platforms:
	cd apps/pipelines && uv run --env-file ../../$(ENV_PATH) python -c \
		"import asyncio; from src.flows.coingecko_platforms import coingecko_sync_platforms_flow; asyncio.run(coingecko_sync_platforms_flow())"

sync-coingecko-coins:
	cd apps/pipelines && uv run --env-file ../../$(ENV_PATH) python -c \
		"import asyncio; from src.flows.coingecko_coins_list import coingecko_sync_coins_list_flow; asyncio.run(coingecko_sync_coins_list_flow())"

# 6. Seeding
seed-data-sources:
	uv run --env-file $(ENV_PATH) python scripts/seed_data_sources_from_csv.py

seed-data-sources-dry:
	uv run --env-file $(ENV_PATH) python scripts/seed_data_sources_from_csv.py --dry-run

seed-metrics-1d:
	uv run --env-file $(ENV_PATH) python scripts/seed_metrics_1d_from_csv.py --csv $(csv) \
		$(if $(source),--source $(source),)

seed-metrics-1d-dry:
	uv run --env-file $(ENV_PATH) python scripts/seed_metrics_1d_from_csv.py --csv $(csv) \
		$(if $(source),--source $(source),) --dry-run

# 7. Asset allow-listing
# Allow-list a coin from coingecko.raw_coins into base.assets + base.asset_source_mappings.
# Requires: coingecko-coins-list-daily flow to have run at least once.
# Usage:
#   make allowlist-asset coingecko_id=bitcoin
#   make allowlist-asset coingecko_id=ethereum cmc_id=1027
#   make allowlist-asset coingecko_id=bitcoin code=BTC name="Bitcoin"
allowlist-asset:
	uv run --env-file $(ENV_PATH) python scripts/allowlist_asset.py \
		--coingecko-id $(coingecko_id) \
		$(if $(cmc_id),--cmc-id $(cmc_id),) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",)

allowlist-asset-dry:
	uv run --env-file $(ENV_PATH) python scripts/allowlist_asset.py \
		--coingecko-id $(coingecko_id) \
		$(if $(cmc_id),--cmc-id $(cmc_id),) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		--dry-run

allowlist-network:
	uv run --env-file $(ENV_PATH) python scripts/allowlist_network.py \
		--coingecko-id $(coingecko_id) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		$(if $(category),--category $(category),)

allowlist-network-dry:
	uv run --env-file $(ENV_PATH) python scripts/allowlist_network.py \
		--coingecko-id $(coingecko_id) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		$(if $(category),--category $(category),) \
		--dry-run

# 8. Bots
setup-paper-trading:
	uv run --env-file $(ENV_PATH) python scripts/setup_paper_trading.py

pricing-bot:
	uv run --env-file $(ENV_PATH) --project apps/bots python -m pricing_bot.main \
		--exchange-id $(or $(exchange),cryptocom) \
		--source-code $(or $(source),exchange:cryptocom) \
		$(foreach p,$(or $(pairs),btc/usdt eth/usdt sol/usdt cro/usdt),--pair $(p))

trader-bot:
	uv run --env-file $(ENV_PATH) --project apps/bots python -m trader_bot.main

manage-trading:
	uv run --env-file $(ENV_PATH) python scripts/manage_trading.py

# 9. Grafana
grafana-ui:
	open http://localhost:3030

# 10. Prefect
# Register all flow deployments with the running Prefect server (reads prefect.yaml)
prefect-deploy:
	cd apps/pipelines && PREFECT_API_URL=http://localhost:4200/api uv run --env-file ../../$(ENV_PATH) prefect --no-prompt deploy --all

# Open the Prefect UI in the default browser
prefect-ui:
	open http://localhost:4200
