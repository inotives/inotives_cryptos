ENV ?= local
ENV_PATH = configs/envs/.env.$(ENV)

# Load variables for shell commands if needed
include $(ENV_PATH)
export $(shell sed 's/=.*//' $(ENV_PATH))

# Helper for dbmate using uvx (ephemeral binary execution)

DB_URL_STR = postgres://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)?sslmode=disable

DBMATE = uv run --env-file $(ENV_PATH) uvx --from dbmate-bin dbmate --url "$(DB_URL_STR)"


.PHONY: init migrate-up migrate-down migrate-status \
        seed-data-sources seed-data-sources-dry \
        seed-metrics-1d seed-metrics-1d-dry \
        sync-coingecko-platforms sync-coingecko-coins \
        allowlist-asset allowlist-asset-dry \
        allowlist-network allowlist-network-dry \
        bootstrap \
        daily-data \
        setup-paper-trading \
        pricing-bot trader-bot \
        manage-trading manage-assets \
        cron-list cron-install cron-remove \
        db-up db-down db-create db-ensure db-status

# 1. Setup the workspace
# initial setup when cloning the repo
init:
	@echo "Bootstrapping inotives..."
	python3 -m venv .venv
	./.venv/bin/pip install uv
	./.venv/bin/uv sync
	@echo "Setup complete. Virtual environment is ready."

# 2. Database — auto-detect external DB or start local PostgreSQL
# Checks if the configured DB_HOST:DB_PORT is reachable.
# If yes, uses the existing database. If no, starts a local Docker PostgreSQL.
db-ensure:
	@python3 -c "import socket; s=socket.create_connection(('$(DB_HOST)', $(DB_PORT)), timeout=2); s.close()" 2>/dev/null \
		&& echo "✓ Database server reachable at $(DB_HOST):$(DB_PORT) — using existing instance." \
		|| (echo "✗ Database server not reachable at $(DB_HOST):$(DB_PORT). Starting local PostgreSQL..." && $(MAKE) db-up)
	@$(MAKE) db-create

db-up:
	docker compose --env-file $(ENV_PATH) up -d db
	@echo "Waiting for PostgreSQL to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		python3 -c "import socket; s=socket.create_connection(('$(DB_HOST)', $(DB_PORT)), timeout=2); s.close()" 2>/dev/null && break || sleep 2; \
	done
	@echo "PostgreSQL is ready at $(DB_HOST):$(DB_PORT)."

db-down:
	docker compose down

db-create:
	@docker exec $(DB_CONTAINER_NAME) psql -U $(DB_USER) -d postgres -tc \
		"SELECT 1 FROM pg_database WHERE datname = '$(DB_NAME)'" | grep -q 1 \
		&& echo "✓ Database '$(DB_NAME)' already exists." \
		|| (docker exec $(DB_CONTAINER_NAME) psql -U $(DB_USER) -d postgres -c "CREATE DATABASE $(DB_NAME);" \
			&& echo "✓ Database '$(DB_NAME)' created.")

db-status:
	@python3 -c "import socket; s=socket.create_connection(('$(DB_HOST)', $(DB_PORT)), timeout=2); s.close(); print('✓ Database is reachable at $(DB_HOST):$(DB_PORT).')" 2>/dev/null \
		|| echo "✗ Database is NOT reachable at $(DB_HOST):$(DB_PORT)."
	@docker compose ps 2>/dev/null || true

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
# then allow-lists a default set of networks and assets.
#
# NOTE: The default networks and assets are a recommended starting point.
# To customise, edit this target or run allowlist-network / allowlist-asset individually:
#   make allowlist-network coingecko_id=binance-smart-chain
#   make allowlist-asset   coingecko_id=solana
# To remove a default, soft-delete the row directly in inotives_tradings.networks or inotives_tradings.assets.
#
# NOTE: Bitcoin does not have a platform entry in CoinGecko (it's not a smart-contract chain).
# Only Ethereum and Solana are allow-listed as networks by default.
bootstrap:
	@echo "--- [1/5] Seeding data sources ---"
	$(MAKE) seed-data-sources
	@echo "--- [2/5] Syncing CoinGecko platforms ---"
	$(MAKE) sync-coingecko-platforms
	@echo "--- [3/5] Syncing CoinGecko coins list ---"
	$(MAKE) sync-coingecko-coins
	@echo "--- [4/5] Allow-listing default networks (ETH, SOL) ---"
	$(MAKE) allowlist-network coingecko_id=ethereum
	$(MAKE) allowlist-network coingecko_id=solana
	@echo "--- [5/5] Allow-listing default assets (BTC, ETH, SOL) ---"
	$(MAKE) allowlist-asset coingecko_id=bitcoin
	$(MAKE) allowlist-asset coingecko_id=ethereum
	$(MAKE) allowlist-asset coingecko_id=solana
	@echo "--- Bootstrap complete. ---"

# Run CoinGecko reference sync directly via bot modules.
# Use these for initial setup or manual refresh.
sync-coingecko-platforms:
	uv run --env-file $(ENV_PATH) python -c \
		"import asyncio; from common.data.coingecko_sync import run_sync_platforms; asyncio.run(run_sync_platforms())"

sync-coingecko-coins:
	uv run --env-file $(ENV_PATH) python -c \
		"import asyncio; from common.data.coingecko_sync import run_sync_coins_list; asyncio.run(run_sync_coins_list())"

# 5. Daily Data Pipeline
# Runs: OHLCV fetch → indicators → regime scores
daily-data:
	uv run --env-file $(ENV_PATH) python -m bots.data_bot.main \
		$(if $(date),--date $(date),)

# 6. Seeding
seed-data-sources:
	uv run --env-file $(ENV_PATH) python db/scripts/seed_data_sources_from_csv.py

seed-data-sources-dry:
	uv run --env-file $(ENV_PATH) python db/scripts/seed_data_sources_from_csv.py --dry-run

seed-metrics-1d:
	uv run --env-file $(ENV_PATH) python db/scripts/seed_metrics_1d_from_csv.py --csv $(csv) \
		$(if $(source),--source $(source),)

seed-metrics-1d-dry:
	uv run --env-file $(ENV_PATH) python db/scripts/seed_metrics_1d_from_csv.py --csv $(csv) \
		$(if $(source),--source $(source),) --dry-run

# 7. Asset allow-listing
# Allow-list a coin from coingecko.raw_coins into inotives_tradings.assets + inotives_tradings.asset_source_mappings.
# Requires: CoinGecko sync to have run at least once.
# Usage:
#   make allowlist-asset coingecko_id=bitcoin
#   make allowlist-asset coingecko_id=ethereum cmc_id=1027
#   make allowlist-asset coingecko_id=bitcoin code=BTC name="Bitcoin"
allowlist-asset:
	uv run --env-file $(ENV_PATH) python -m common.tools.allowlist_asset \
		--coingecko-id $(coingecko_id) \
		$(if $(cmc_id),--cmc-id $(cmc_id),) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",)

allowlist-asset-dry:
	uv run --env-file $(ENV_PATH) python -m common.tools.allowlist_asset \
		--coingecko-id $(coingecko_id) \
		$(if $(cmc_id),--cmc-id $(cmc_id),) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		--dry-run

allowlist-network:
	uv run --env-file $(ENV_PATH) python -m common.tools.allowlist_network \
		--coingecko-id $(coingecko_id) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		$(if $(category),--category $(category),)

allowlist-network-dry:
	uv run --env-file $(ENV_PATH) python -m common.tools.allowlist_network \
		--coingecko-id $(coingecko_id) \
		$(if $(code),--code $(code),) \
		$(if $(name),--name "$(name)",) \
		$(if $(category),--category $(category),) \
		--dry-run

# 8. Bots
setup-paper-trading:
	uv run --env-file $(ENV_PATH) python -m common.tools.setup_paper_trading

pricing-bot:
	uv run --env-file $(ENV_PATH) python -m bots.pricing_bot.main \
		--exchange-id $(or $(exchange),cryptocom) \
		--source-code $(or $(source),exchange:cryptocom) \
		$(foreach p,$(or $(pairs),btc/usdt eth/usdt sol/usdt cro/usdt),--pair $(p))

trader-bot:
	uv run --env-file $(ENV_PATH) python -m bots.trader_bot.main \
		--market $(or $(market),btc/usdt) \
		$(if $(exchange),--exchange $(exchange),) \
		$(if $(paper),--paper,) \
		$(if $(interval),--poll-interval $(interval),)

manage-trading:
	uv run --env-file $(ENV_PATH) python -m common.tools.manage_trading

manage-assets:
	uv run --env-file $(ENV_PATH) python -m common.tools.manage_assets $(cmd)

# 9. Cron management
cron-list:
	python -m common.tools.manage_cron list

cron-install:
	python -m common.tools.manage_cron install $(job)

cron-remove:
	python -m common.tools.manage_cron remove $(job)
