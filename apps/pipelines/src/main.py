"""
Register all pipeline deployments with the Prefect server.

Deployments are defined in prefect.yaml (project root for this app).
This module exists as a convenience entry point — `make prefect-deploy`
calls `prefect deploy --all` from the pipelines directory instead.

Flows registered:
  coingecko-asset-platforms-daily  →  daily at 01:00 UTC
  daily-data-pipeline              →  daily at 02:00 UTC
"""

# Import flows here to ensure they are importable (used as a sanity check).
from src.flows.coingecko import fetch_asset_platforms_flow          # noqa: F401
from src.flows.daily_pipeline import daily_pipeline_flow            # noqa: F401

if __name__ == "__main__":
    print("Use `make prefect-deploy` to register deployments via prefect.yaml.")
    print()
    print("Registered flows:")
    print("  coingecko-asset-platforms-daily  →  daily at 01:00 UTC")
    print("  daily-data-pipeline              →  daily at 02:00 UTC")
