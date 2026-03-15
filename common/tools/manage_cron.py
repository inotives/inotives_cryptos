"""
Cron job manager for inotives scheduled tasks.

Manages crontab entries for:
  - daily-data    : OHLCV → indicators → regime scores (02:00 UTC daily)
  - coingecko-sync: Platforms + coins list refresh (01:00 UTC weekly, Sunday)

Usage:
    python -m common.tools.manage_cron list                  # Show active inotives cron jobs
    python -m common.tools.manage_cron install               # Install all default cron jobs
    python -m common.tools.manage_cron install daily-data    # Install only daily-data job
    python -m common.tools.manage_cron install coingecko-sync
    python -m common.tools.manage_cron remove                # Remove all inotives cron jobs
    python -m common.tools.manage_cron remove daily-data     # Remove only daily-data job

All cron entries are tagged with '# inotives:<job_name>' for safe identification.
Logs are written to logs/<job_name>.log (auto-created).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
ENV_FILE = "configs/envs/.env.local"

# Tag prefix used to identify our cron entries
CRON_TAG = "# inotives:"

# uv run prefix — all commands run via uv with the project env
UV_RUN = f"cd {PROJECT_DIR} && uv run --env-file {ENV_FILE}"

# Job definitions: name → (schedule, command, description)
JOBS = {
    "daily-data": {
        "schedule": "0 2 * * *",
        "command": f"{UV_RUN} python -m bots.data_bot.main",
        "description": "Daily OHLCV → indicators → regime scores (02:00 UTC)",
    },
    "coingecko-sync": {
        "schedule": "0 1 * * 0",
        "command": (
            f"{UV_RUN} python -c "
            "\"import asyncio; "
            "from common.data.coingecko_sync import run_sync_platforms, run_sync_coins_list; "
            "asyncio.run(run_sync_platforms()); "
            "asyncio.run(run_sync_coins_list())\""
        ),
        "description": "CoinGecko platforms + coins list sync (01:00 UTC Sunday)",
    },
}


# ---------------------------------------------------------------------------
# Crontab helpers
# ---------------------------------------------------------------------------

def _read_crontab() -> str:
    """Read the current user's crontab. Returns empty string if none."""
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _write_crontab(content: str) -> None:
    """Write the full crontab content."""
    proc = subprocess.run(
        ["crontab", "-"], input=content, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"Error writing crontab: {proc.stderr}", file=sys.stderr)
        sys.exit(1)


def _cron_line(job_name: str) -> str:
    """Build a full cron line with logging and tag."""
    job = JOBS[job_name]
    log_file = LOG_DIR / f"{job_name}.log"
    return (
        f"{job['schedule']} "
        f"{job['command']} "
        f">> {log_file} 2>&1 "
        f"{CRON_TAG}{job_name}"
    )


def _filter_lines(lines: list[str], job_name: str | None = None) -> tuple[list[str], list[str]]:
    """
    Split crontab lines into (ours, others).
    If job_name is given, only match that specific job.
    """
    ours, others = [], []
    for line in lines:
        tag = f"{CRON_TAG}{job_name}" if job_name else CRON_TAG
        if tag in line:
            ours.append(line)
        else:
            others.append(line)
    return ours, others


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list() -> None:
    """Show active inotives cron jobs."""
    crontab = _read_crontab()
    lines = crontab.splitlines()
    ours, _ = _filter_lines(lines)

    if not ours:
        print("No inotives cron jobs found.")
        return

    print(f"Active inotives cron jobs ({len(ours)}):\n")
    for line in ours:
        # Extract job name from tag
        tag_idx = line.find(CRON_TAG)
        if tag_idx >= 0:
            name = line[tag_idx + len(CRON_TAG):]
            job = JOBS.get(name, {})
            desc = job.get("description", "")
            print(f"  [{name}] {desc}")
            # Show schedule portion
            schedule_end = line.find(str(PROJECT_DIR))
            if schedule_end > 0:
                print(f"    schedule: {line[:schedule_end].strip()}")
            print()


def cmd_install(job_name: str | None = None, dry_run: bool = False) -> None:
    """Install cron jobs. If job_name is None, install all."""
    jobs_to_install = [job_name] if job_name else list(JOBS.keys())

    # Validate
    for name in jobs_to_install:
        if name not in JOBS:
            print(f"Unknown job: {name}")
            print(f"Available: {', '.join(JOBS.keys())}")
            sys.exit(1)

    if dry_run:
        print("Cron entries that would be installed:\n")
        for name in jobs_to_install:
            job = JOBS[name]
            print(f"  [{name}] {job['description']}")
            print(f"    {_cron_line(name)}")
            print()
        print("Run without --dry-run to install.")
        return

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    crontab = _read_crontab()
    lines = crontab.splitlines()

    for name in jobs_to_install:
        # Remove existing entry for this job (if any)
        _, lines = _filter_lines(lines, name)
        # Add new entry
        lines.append(_cron_line(name))

    # Write back
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    _write_crontab(content)

    for name in jobs_to_install:
        job = JOBS[name]
        print(f"  Installed: [{name}] {job['description']}")
        print(f"    schedule: {job['schedule']}")
        print(f"    log: {LOG_DIR / f'{name}.log'}")
        print()

    print("Done. Verify with: python -m common.tools.manage_cron list")


def cmd_remove(job_name: str | None = None) -> None:
    """Remove cron jobs. If job_name is None, remove all inotives jobs."""
    crontab = _read_crontab()
    lines = crontab.splitlines()

    if job_name:
        removed, remaining = _filter_lines(lines, job_name)
    else:
        removed, remaining = _filter_lines(lines)

    if not removed:
        target = f"'{job_name}'" if job_name else "inotives"
        print(f"No {target} cron jobs found to remove.")
        return

    content = "\n".join(remaining)
    if content and not content.endswith("\n"):
        content += "\n"
    _write_crontab(content if content.strip() else "")

    for line in removed:
        tag_idx = line.find(CRON_TAG)
        name = line[tag_idx + len(CRON_TAG):] if tag_idx >= 0 else "unknown"
        print(f"  Removed: [{name}]")

    print(f"\n{len(removed)} job(s) removed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Manage cron jobs for inotives scheduled tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available jobs:
  daily-data       Daily OHLCV → indicators → regime scores (02:00 UTC)
  coingecko-sync   CoinGecko platforms + coins list sync (01:00 UTC Sunday)

Examples:
  %(prog)s list                    Show active jobs
  %(prog)s install                 Install all jobs
  %(prog)s install daily-data      Install only daily-data
  %(prog)s remove                  Remove all inotives jobs
  %(prog)s remove coingecko-sync   Remove specific job
        """,
    )
    p.add_argument(
        "action",
        choices=["list", "install", "remove"],
        help="Action to perform",
    )
    p.add_argument(
        "job",
        nargs="?",
        default=None,
        help="Specific job name (optional — defaults to all jobs)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = p.parse_args()

    if args.action == "list":
        cmd_list()
    elif args.action == "install":
        cmd_install(args.job, dry_run=args.dry_run)
    elif args.action == "remove":
        cmd_remove(args.job)


if __name__ == "__main__":
    main()
