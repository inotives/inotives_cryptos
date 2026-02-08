ENV ?= local

# Dynamically point to the hidden folder
ENV_FILE = .envs/.env.$(ENV)

.PHONY: api dbt migrate

api:
    uv run --env-file $(ENV_FILE) --package api fastapi dev src/main.py

dbt-run:
    uv run --env-file configs/envs/.env.$(ENV) --package analytics dbt run

migrate:
    uvx --env-file $(ENV_FILE) dbmate up