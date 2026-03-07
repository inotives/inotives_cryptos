ENV ?= local
ENV_PATH = configs/envs/.env.$(ENV)

# Load variables for shell commands if needed
include $(ENV_PATH)
export $(shell sed 's/=.*//' $(ENV_PATH))

# Helper for dbmate using uvx (ephemeral binary execution)

DB_URL_STR = postgres://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)?sslmode=disable

DBMATE = uv run --env-file $(ENV_PATH) uvx --from dbmate-bin dbmate --url "$(DB_URL_STR)"


.PHONY: init devon services-up services-down migrate-up migrate-down migrate-status

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
