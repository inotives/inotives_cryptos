"""
CLI for managing trade strategies and trade cycles.

Supports two modes:
  1. Interactive (no arguments) — menu-driven TUI for manual management
  2. Non-interactive (subcommands) — scriptable for automation / OpenClaw

Non-interactive usage:
    python -m common.tools.manage_trading list-strategies
    python -m common.tools.manage_trading view-strategy   --strategy-id 5
    python -m common.tools.manage_trading activate         --strategy-id 5
    python -m common.tools.manage_trading pause            --strategy-id 5
    python -m common.tools.manage_trading archive          --strategy-id 5
    python -m common.tools.manage_trading update           --strategy-id 5 --param capital_per_cycle=2000
    python -m common.tools.manage_trading delete-strategy  --strategy-id 5
    python -m common.tools.manage_trading list-cycles      --strategy-id 5
    python -m common.tools.manage_trading view-cycle       --cycle-id 10
    python -m common.tools.manage_trading cancel-cycle     --cycle-id 10
    python -m common.tools.manage_trading close-cycle      --cycle-id 10

Interactive usage:
    python -m common.tools.manage_trading
    python -m common.tools.manage_trading interactive
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

from common.config import settings

# ── ANSI colours ──────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
WHITE  = "\033[97m"


def c(text, colour):  return f"{colour}{text}{RESET}"
def bold(text):       return c(text, BOLD)
def header(text):     print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}\n{BOLD}{CYAN}  {text}{RESET}\n{DIM}{'─' * 50}{RESET}")
def success(text):    print(f"  {GREEN}✓ {text}{RESET}")
def warn(text):       print(f"  {YELLOW}⚠ {text}{RESET}")
def error(text):      print(f"  {RED}✗ {text}{RESET}")
def info(text):       print(f"  {DIM}{text}{RESET}")


# ── DB ────────────────────────────────────────────────────────────────────────

async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.db_dsn)


# ── Input helpers (interactive mode only) ─────────────────────────────────────

def prompt(label: str, default: str = "") -> str:
    dflt = f" [{default}]" if default else ""
    val  = input(f"  {label}{dflt}: ").strip()
    return val if val else default

def prompt_int(label: str, default: int | None = None) -> int | None:
    dflt = str(default) if default is not None else ""
    raw  = prompt(label, dflt)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default

def prompt_float(label: str, default: float) -> float:
    raw = prompt(label, str(default))
    try:
        return float(raw)
    except ValueError:
        return default

def prompt_bool(label: str, default: bool = True) -> bool:
    dflt = "Y/n" if default else "y/N"
    raw  = prompt(f"{label} ({dflt})", "").lower()
    if raw in ("y", "yes"):  return True
    if raw in ("n", "no"):   return False
    return default

def menu(options: list[str]) -> str:
    for i, opt in enumerate(options, 1):
        print(f"  {BOLD}{i}{RESET}. {opt}")
    print(f"  {DIM}0. Back / Exit{RESET}")
    return input(f"\n  {BOLD}Choice:{RESET} ").strip()


# ── Default DCA_GRID parameter set ────────────────────────────────────────────

DCA_GRID_DEFAULTS = {
    "capital_per_cycle":          1000,
    "num_levels":                 5,
    "weights":                    [1, 1, 2, 3, 3],
    "atr_multiplier_low":         0.4,
    "atr_multiplier_normal":      0.5,
    "atr_multiplier_high":        0.7,
    "profit_target_low":          1.0,
    "profit_target_normal":       1.5,
    "profit_target_high":         2.5,
    "max_atr_pct_entry":          6.0,
    "rsi_entry_max":              60,
    "reserve_capital_pct":        30,
    "taker_fee_pct":              0.001,
    "circuit_breaker_atr_pct":    8.0,
    "max_expansions":             1,
    "expansion_levels":           2,
    "expansion_capital_fraction": 0.3,
    "require_uptrend":            True,
    "require_golden_cross":       True,
    "force_entry":                False,
    "defensive_mode_enabled":    False,
    "defensive_atr_multiplier":  0.8,
    "defensive_profit_target":   2.5,
    "defensive_num_levels":      5,
    "defensive_rsi_oversold":    40,
    "defensive_rsi_timeframe":   "1h",
    "defensive_rsi_period":      14,
}


# ── Status colour helpers ─────────────────────────────────────────────────────

def strategy_status_colour(status: str) -> str:
    return {
        "ACTIVE":   GREEN,
        "PAUSED":   YELLOW,
        "ARCHIVED": DIM,
    }.get(status, WHITE)

def cycle_status_colour(status: str) -> str:
    return {
        "OPEN":      GREEN,
        "CLOSING":   YELLOW,
        "CLOSED":    DIM,
        "CANCELLED": RED,
    }.get(status, WHITE)

def level_status_colour(status: str) -> str:
    return {
        "FILLED":    GREEN,
        "OPEN":      CYAN,
        "PENDING":   YELLOW,
        "CANCELLED": DIM,
    }.get(status, WHITE)


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED QUERIES
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_strategies(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT ts.id, ts.name, ts.strategy_type, ts.status,
               ts.taker_fee_pct, ts.created_at,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset,
               v.name       AS venue
        FROM inotives_tradings.trade_strategies ts
        JOIN inotives_tradings.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN inotives_tradings.assets a_quote ON a_quote.id = ts.quote_asset_id
        JOIN inotives_tradings.venues v       ON v.id        = ts.venue_id
        WHERE ts.deleted_at IS NULL
        ORDER BY ts.id
        """
    )
    return [dict(r) for r in rows]


async def _fetch_strategy_detail(conn: asyncpg.Connection, sid: int) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT ts.*,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset,
               v.name       AS venue_name
        FROM inotives_tradings.trade_strategies ts
        JOIN inotives_tradings.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN inotives_tradings.assets a_quote ON a_quote.id = ts.quote_asset_id
        JOIN inotives_tradings.venues v       ON v.id        = ts.venue_id
        WHERE ts.id = $1 AND ts.deleted_at IS NULL
        """,
        sid,
    )
    return dict(row) if row else None


async def _set_strategy_status(conn: asyncpg.Connection, sid: int, new_status: str) -> bool:
    """Set strategy status. Returns True on success, False if not found."""
    row = await conn.fetchrow(
        "SELECT id, status FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        return False
    if row["status"] == new_status:
        return True  # already in target state
    await conn.execute(
        "UPDATE inotives_tradings.trade_strategies SET status=$1::inotives_tradings.trade_strategy_status, updated_at=NOW() WHERE id=$2",
        new_status, sid,
    )
    return True


async def _update_strategy_params(conn: asyncpg.Connection, sid: int, params: dict) -> bool:
    """Merge params into strategy metadata. Returns True on success."""
    row = await conn.fetchrow(
        "SELECT metadata FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        return False
    meta = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"] or "{}")
    meta.update(params)
    await conn.execute(
        "UPDATE inotives_tradings.trade_strategies SET metadata=$1, updated_at=NOW() WHERE id=$2",
        json.dumps(meta), sid,
    )
    return True


async def _delete_strategy(conn: asyncpg.Connection, sid: int) -> tuple[bool, str]:
    """Soft-delete a strategy. Returns (success, message)."""
    row = await conn.fetchrow(
        "SELECT id, name FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        return False, f"Strategy {sid} not found."
    open_cycles = await conn.fetchval(
        "SELECT COUNT(*) FROM inotives_tradings.trade_cycles WHERE strategy_id=$1 AND status='OPEN' AND deleted_at IS NULL",
        sid,
    )
    if open_cycles > 0:
        return False, f"Strategy has {open_cycles} open cycle(s). Close or cancel them first."
    await conn.execute("DELETE FROM inotives_tradings.trade_strategies WHERE id=$1", sid)
    return True, f"Strategy {sid} ({row['name']}) deleted."


async def _fetch_cycles(conn: asyncpg.Connection, sid: int, limit: int = 30) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT tc.id, tc.cycle_number, tc.status, tc.capital_allocated,
               tc.close_trigger, tc.opened_at, tc.closed_at, tc.stop_loss_price,
               dd.profit_target_pct, dd.volatility_regime, dd.grid_spacing_pct
        FROM inotives_tradings.trade_cycles tc
        LEFT JOIN inotives_tradings.trade_dca_cycle_details dd ON dd.cycle_id = tc.id
        WHERE tc.strategy_id=$1 AND tc.deleted_at IS NULL
        ORDER BY tc.cycle_number DESC
        LIMIT $2
        """,
        sid, limit,
    )
    return [dict(r) for r in rows]


async def _fetch_cycle_detail(conn: asyncpg.Connection, cid: int) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT tc.*,
               ts.name AS strategy_name,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset
        FROM inotives_tradings.trade_cycles tc
        JOIN inotives_tradings.trade_strategies ts ON ts.id = tc.strategy_id
        JOIN inotives_tradings.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN inotives_tradings.assets a_quote ON a_quote.id = ts.quote_asset_id
        WHERE tc.id=$1 AND tc.deleted_at IS NULL
        """,
        cid,
    )
    return dict(row) if row else None


async def _close_or_cancel_cycle(conn: asyncpg.Connection, cid: int, action: str) -> tuple[bool, str]:
    """
    Cancel or force-close a cycle.
    action: 'cancel' → CANCELLED, 'close' → CLOSED
    Returns (success, message).
    """
    cycle = await conn.fetchrow(
        "SELECT id, cycle_number, status FROM inotives_tradings.trade_cycles WHERE id=$1 AND deleted_at IS NULL",
        cid,
    )
    if not cycle:
        return False, f"Cycle {cid} not found."
    if cycle["status"] not in ("OPEN", "CLOSING"):
        return False, f"Cycle is already {cycle['status']}."

    now = datetime.now(timezone.utc)
    new_status = "CANCELLED" if action == "cancel" else "CLOSED"

    await conn.execute(
        """
        UPDATE inotives_tradings.trade_grid_levels
        SET status='CANCELLED', updated_at=NOW()
        WHERE cycle_id=$1 AND status IN ('PENDING','OPEN')
        """,
        cid,
    )
    await conn.execute(
        """
        UPDATE inotives_tradings.trade_cycles
        SET status=$1::inotives_tradings.trade_cycle_status, close_trigger='manual', closed_at=$2, updated_at=NOW()
        WHERE id=$3
        """,
        new_status, now, cid,
    )
    # Release capital locks
    await conn.execute(
        """
        UPDATE inotives_tradings.capital_locks
        SET status='RELEASED', released_at=NOW(), updated_at=NOW()
        WHERE cycle_id=$1 AND status='ACTIVE'
        """,
        cid,
    )
    verb = "cancelled" if action == "cancel" else "force-closed"
    return True, f"Cycle {cid} (#{cycle['cycle_number']}) {verb}."


# ══════════════════════════════════════════════════════════════════════════════
#  NON-INTERACTIVE SUBCOMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_list_strategies(args) -> int:
    conn = await connect()
    try:
        rows = await _fetch_strategies(conn)
        if not rows:
            print("No strategies found.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'ID':<5} {'Name':<30} {'Type':<14} {'Pair':<12} {'Venue':<22} {'Status'}")
            print("─" * 90)
            for r in rows:
                pair = f"{r['base_asset'].upper()}/{r['quote_asset'].upper()}"
                print(f"{r['id']:<5} {r['name']:<30} {r['strategy_type']:<14} {pair:<12} {r['venue']:<22} {r['status']}")
    finally:
        await conn.close()
    return 0


async def cmd_view_strategy(args) -> int:
    conn = await connect()
    try:
        r = await _fetch_strategy_detail(conn, args.strategy_id)
        if not r:
            print(f"Strategy {args.strategy_id} not found.", file=sys.stderr)
            return 1
        meta = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}")

        if args.json:
            output = {
                "id": r["id"], "name": r["name"], "description": r["description"],
                "strategy_type": r["strategy_type"], "status": r["status"],
                "pair": f"{r['base_asset'].upper()}/{r['quote_asset'].upper()}",
                "venue": r["venue_name"],
                "maker_fee_pct": float(r["maker_fee_pct"]) if r["maker_fee_pct"] else None,
                "taker_fee_pct": float(r["taker_fee_pct"]) if r["taker_fee_pct"] else None,
                "created_at": str(r["created_at"]),
                "metadata": meta,
            }
            counts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='OPEN')      AS open_count,
                    COUNT(*) FILTER (WHERE status='CLOSED')    AS closed_count,
                    COUNT(*) FILTER (WHERE status='CANCELLED') AS cancelled_count
                FROM inotives_tradings.trade_cycles WHERE strategy_id=$1 AND deleted_at IS NULL
                """,
                args.strategy_id,
            )
            output["cycles"] = dict(counts)
            print(json.dumps(output, default=str, indent=2))
        else:
            print(f"ID:          {r['id']}")
            print(f"Name:        {r['name']}")
            print(f"Description: {r['description'] or '—'}")
            print(f"Type:        {r['strategy_type']}")
            print(f"Pair:        {r['base_asset'].upper()}/{r['quote_asset'].upper()}")
            print(f"Venue:       {r['venue_name']}")
            print(f"Fees:        maker={r['maker_fee_pct']}  taker={r['taker_fee_pct']}")
            print(f"Status:      {r['status']}")
            print(f"Created:     {r['created_at'].strftime('%Y-%m-%d %H:%M')}")
            print(f"\nParameters:")
            for k, v in meta.items():
                print(f"  {k:<35} {v}")
    finally:
        await conn.close()
    return 0


async def cmd_set_status(args) -> int:
    new_status = args.command.upper()  # activate→ACTIVE, pause→PAUSED, archive→ARCHIVED
    status_map = {"ACTIVATE": "ACTIVE", "PAUSE": "PAUSED", "ARCHIVE": "ARCHIVED"}
    new_status = status_map.get(new_status, new_status)

    conn = await connect()
    try:
        ok = await _set_strategy_status(conn, args.strategy_id, new_status)
        if not ok:
            print(f"Strategy {args.strategy_id} not found.", file=sys.stderr)
            return 1
        print(f"Strategy {args.strategy_id} → {new_status}")
    finally:
        await conn.close()
    return 0


async def cmd_update(args) -> int:
    if not args.param:
        print("No --param provided. Use --param key=value (repeatable).", file=sys.stderr)
        return 1

    params = {}
    for p in args.param:
        if "=" not in p:
            print(f"Invalid param format: '{p}'. Expected key=value.", file=sys.stderr)
            return 1
        key, val = p.split("=", 1)
        # Try to parse as JSON value (handles numbers, bools, lists)
        try:
            params[key] = json.loads(val)
        except json.JSONDecodeError:
            params[key] = val  # keep as string

    conn = await connect()
    try:
        ok = await _update_strategy_params(conn, args.strategy_id, params)
        if not ok:
            print(f"Strategy {args.strategy_id} not found.", file=sys.stderr)
            return 1
        print(f"Strategy {args.strategy_id} updated: {', '.join(f'{k}={v}' for k, v in params.items())}")
    finally:
        await conn.close()
    return 0


async def cmd_delete_strategy(args) -> int:
    conn = await connect()
    try:
        ok, msg = await _delete_strategy(conn, args.strategy_id)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_list_cycles(args) -> int:
    conn = await connect()
    try:
        rows = await _fetch_cycles(conn, args.strategy_id, limit=args.limit)
        if not rows:
            print(f"No cycles found for strategy {args.strategy_id}.")
            return 0
        if args.json:
            print(json.dumps(rows, default=str, indent=2))
        else:
            print(f"{'ID':<6} {'#':<4} {'Status':<11} {'Capital':>10} {'Opened':<18} {'Closed':<18} {'Trigger':<16} {'Regime'}")
            print("─" * 100)
            for r in rows:
                opened  = r["opened_at"].strftime("%Y-%m-%d %H:%M") if r["opened_at"] else "—"
                closed  = r["closed_at"].strftime("%Y-%m-%d %H:%M") if r["closed_at"] else "—"
                trigger = r["close_trigger"] or "—"
                regime  = r["volatility_regime"] or "—"
                capital = f"${float(r['capital_allocated']):,.2f}"
                print(
                    f"{r['id']:<6} {r['cycle_number']:<4} {r['status']:<11} "
                    f"{capital:>10}  {opened:<18} {closed:<18} {trigger:<16} {regime}"
                )
    finally:
        await conn.close()
    return 0


async def cmd_view_cycle(args) -> int:
    conn = await connect()
    try:
        cycle = await _fetch_cycle_detail(conn, args.cycle_id)
        if not cycle:
            print(f"Cycle {args.cycle_id} not found.", file=sys.stderr)
            return 1

        dd = await conn.fetchrow(
            "SELECT * FROM inotives_tradings.trade_dca_cycle_details WHERE cycle_id=$1", args.cycle_id
        )
        levels = await conn.fetch(
            """
            SELECT gl.level_num, gl.target_price, gl.quantity, gl.capital_allocated,
                   gl.weight, gl.status, gl.filled_at, gl.level_trigger,
                   o.avg_fill_price
            FROM inotives_tradings.trade_grid_levels gl
            LEFT JOIN inotives_tradings.trade_orders o ON o.id = gl.order_id
            WHERE gl.cycle_id=$1
            ORDER BY gl.level_num
            """,
            args.cycle_id,
        )
        pnl = await conn.fetchrow("SELECT * FROM inotives_tradings.trade_pnl WHERE cycle_id=$1", args.cycle_id)

        if args.json:
            output = {
                "id": cycle["id"],
                "cycle_number": cycle["cycle_number"],
                "strategy_name": cycle["strategy_name"],
                "pair": f"{cycle['base_asset'].upper()}/{cycle['quote_asset'].upper()}",
                "status": cycle["status"],
                "capital_allocated": float(cycle["capital_allocated"]),
                "stop_loss_price": float(cycle["stop_loss_price"]) if cycle.get("stop_loss_price") else None,
                "opened_at": str(cycle["opened_at"]),
                "closed_at": str(cycle["closed_at"]) if cycle["closed_at"] else None,
                "close_trigger": cycle["close_trigger"],
            }
            if dd:
                output["dca_details"] = {
                    "volatility_regime": dd["volatility_regime"],
                    "grid_spacing_pct": float(dd["grid_spacing_pct"]),
                    "profit_target_pct": float(dd["profit_target_pct"]),
                    "atr_at_open": float(dd["atr_at_open"]),
                }
            if levels:
                output["levels"] = [
                    {
                        "level_num": lv["level_num"],
                        "target_price": float(lv["target_price"]),
                        "quantity": float(lv["quantity"]),
                        "status": lv["status"],
                        "fill_price": float(lv["avg_fill_price"]) if lv["avg_fill_price"] else None,
                    }
                    for lv in levels
                ]
            if pnl:
                output["pnl"] = {
                    "gross_pnl": float(pnl["gross_pnl"]),
                    "net_pnl": float(pnl["net_pnl"]),
                    "pnl_pct": float(pnl["pnl_pct"]),
                    "total_fees": float(pnl["total_fees"]),
                }
            print(json.dumps(output, default=str, indent=2))
        else:
            print(f"Cycle #{cycle['cycle_number']}  —  {cycle['strategy_name']}")
            print(f"ID:             {cycle['id']}")
            print(f"Pair:           {cycle['base_asset'].upper()}/{cycle['quote_asset'].upper()}")
            print(f"Status:         {cycle['status']}")
            print(f"Capital:        ${float(cycle['capital_allocated']):,.2f}")
            if cycle.get("stop_loss_price"):
                print(f"Stop Loss:      {float(cycle['stop_loss_price']):,.2f}")
            print(f"Opened:         {cycle['opened_at'].strftime('%Y-%m-%d %H:%M:%S')}")
            if cycle["closed_at"]:
                print(f"Closed:         {cycle['closed_at'].strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Close trigger:  {cycle['close_trigger']}")

            if dd:
                print(f"\nDCA Details:")
                print(f"  Regime:         {dd['volatility_regime']}")
                print(f"  Grid spacing:   {float(dd['grid_spacing_pct']):.3f}%")
                print(f"  Profit target:  {float(dd['profit_target_pct']):.2f}%")
                print(f"  ATR at open:    {float(dd['atr_at_open']):,.2f}")

            if levels:
                print(f"\nGrid Levels:")
                print(f"  {'Lvl':<4} {'Target':>12} {'Qty':>14} {'Capital':>10} {'Fill Price':>12} {'Status':<10} Trigger")
                print(f"  {'─'*80}")
                for lv in levels:
                    fill_px = f"{float(lv['avg_fill_price']):,.2f}" if lv["avg_fill_price"] else "—"
                    print(
                        f"  {lv['level_num']:<4} {float(lv['target_price']):>12,.2f} "
                        f"{float(lv['quantity']):>14.6f} ${float(lv['capital_allocated']):>9,.2f} "
                        f"{fill_px:>12} {lv['status']:<10} {lv['level_trigger'] or 'initial'}"
                    )

            if pnl:
                print(f"\nP&L:")
                print(f"  Gross: ${float(pnl['gross_pnl']):,.4f}")
                print(f"  Fees:  ${float(pnl['total_fees']):,.4f}")
                print(f"  Net:   ${float(pnl['net_pnl']):,.4f}  ({float(pnl['pnl_pct']):.3f}%)")
    finally:
        await conn.close()
    return 0


async def cmd_cancel_cycle(args) -> int:
    conn = await connect()
    try:
        ok, msg = await _close_or_cancel_cycle(conn, args.cycle_id, "cancel")
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
    finally:
        await conn.close()
    return 0


async def cmd_close_cycle(args) -> int:
    conn = await connect()
    try:
        ok, msg = await _close_or_cancel_cycle(conn, args.cycle_id, "close")
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
    finally:
        await conn.close()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  INTERACTIVE MODE (original menu-driven TUI)
# ══════════════════════════════════════════════════════════════════════════════

async def show_strategies_interactive(conn: asyncpg.Connection) -> None:
    header("Strategies")
    rows = await _fetch_strategies(conn)
    if not rows:
        warn("No strategies found.")
        return
    print(f"  {BOLD}{'ID':<5} {'Name':<30} {'Type':<12} {'Pair':<12} {'Venue':<22} Status{RESET}")
    print(f"  {DIM}{'─'*90}{RESET}")
    for r in rows:
        sc   = strategy_status_colour(r["status"])
        pair = f"{r['base_asset'].upper()}/{r['quote_asset'].upper()}"
        print(
            f"  {BOLD}{r['id']:<5}{RESET} {r['name']:<30} {r['strategy_type']:<12} "
            f"{pair:<12} {r['venue']:<22} {sc}{r['status']}{RESET}"
        )


async def view_strategy_interactive(conn: asyncpg.Connection) -> None:
    header("View Strategy")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    r = await _fetch_strategy_detail(conn, sid)
    if not r:
        error(f"Strategy {sid} not found.")
        return
    sc = strategy_status_colour(r["status"])
    meta = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}")

    print(f"\n  {BOLD}ID:{RESET}          {r['id']}")
    print(f"  {BOLD}Name:{RESET}        {r['name']}")
    print(f"  {BOLD}Description:{RESET} {r['description'] or '—'}")
    print(f"  {BOLD}Type:{RESET}        {r['strategy_type']}")
    print(f"  {BOLD}Pair:{RESET}        {r['base_asset'].upper()}/{r['quote_asset'].upper()}")
    print(f"  {BOLD}Venue:{RESET}       {r['venue_name']}")
    print(f"  {BOLD}Fees:{RESET}        maker={r['maker_fee_pct']}  taker={r['taker_fee_pct']}")
    print(f"  {BOLD}Status:{RESET}      {sc}{r['status']}{RESET}")
    print(f"  {BOLD}Created:{RESET}     {r['created_at'].strftime('%Y-%m-%d %H:%M')}")
    print(f"\n  {BOLD}Parameters (metadata):{RESET}")
    for k, v in meta.items():
        print(f"    {DIM}{k:<35}{RESET} {v}")

    counts = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status='OPEN')      AS open_count,
            COUNT(*) FILTER (WHERE status='CLOSED')    AS closed_count,
            COUNT(*) FILTER (WHERE status='CANCELLED') AS cancelled_count
        FROM inotives_tradings.trade_cycles WHERE strategy_id=$1 AND deleted_at IS NULL
        """,
        sid,
    )
    print(f"\n  {BOLD}Cycles:{RESET}      "
          f"{GREEN}open={counts['open_count']}{RESET}  "
          f"{DIM}closed={counts['closed_count']}  cancelled={counts['cancelled_count']}{RESET}")


async def create_strategy_interactive(conn: asyncpg.Connection) -> None:
    header("Create Strategy")

    venues = await conn.fetch("SELECT id, name, venue_type FROM inotives_tradings.venues WHERE deleted_at IS NULL ORDER BY id")
    if not venues:
        error("No venues found. Run 'make setup-paper-trading' first.")
        return
    print(f"\n  {BOLD}Available venues:{RESET}")
    for v in venues:
        print(f"    {v['id']}. {v['name']}  ({v['venue_type']})")
    venue_id = prompt_int("Venue ID")
    if venue_id is None or not any(v["id"] == venue_id for v in venues):
        error("Invalid venue.")
        return

    base_code  = prompt("Base asset code (e.g. btc)").lower()
    quote_code = prompt("Quote asset code (e.g. usdt)").lower()
    base_row  = await conn.fetchrow("SELECT id FROM inotives_tradings.assets WHERE code=$1 AND deleted_at IS NULL", base_code)
    quote_row = await conn.fetchrow("SELECT id FROM inotives_tradings.assets WHERE code=$1 AND deleted_at IS NULL", quote_code)
    if not base_row:
        error(f"Asset '{base_code}' not found.")
        return
    if not quote_row:
        error(f"Asset '{quote_code}' not found.")
        return

    name        = prompt("Strategy name", f"{base_code.upper()}/{quote_code.upper()} DCA Grid")
    description = prompt("Description (optional)", "")
    taker_fee   = prompt_float("Taker fee % (e.g. 0.001)", 0.001)

    print(f"\n  {BOLD}DCA Grid parameters{RESET} — press Enter to keep defaults:")
    meta = {}
    for k, default in DCA_GRID_DEFAULTS.items():
        if k == "weights":
            raw = prompt(f"  weights (comma-separated)", ",".join(str(w) for w in default))
            try:
                meta[k] = [int(x.strip()) for x in raw.split(",")]
            except ValueError:
                meta[k] = default
        elif isinstance(default, bool):
            meta[k] = prompt_bool(f"  {k}", default)
        elif isinstance(default, int):
            meta[k] = prompt_int(f"  {k}", default)
        elif isinstance(default, float):
            meta[k] = prompt_float(f"  {k}", default)
        else:
            meta[k] = default

    print(f"\n  {BOLD}Summary:{RESET}")
    print(f"    Pair:   {base_code.upper()}/{quote_code.upper()}")
    print(f"    Name:   {name}")
    print(f"    Status: ACTIVE")
    if not prompt_bool("Confirm create?"):
        warn("Cancelled.")
        return

    sid = await conn.fetchval(
        """
        INSERT INTO inotives_tradings.trade_strategies
            (name, description, strategy_type, base_asset_id, quote_asset_id,
             venue_id, taker_fee_pct, status, metadata)
        VALUES ($1, $2, 'DCA_GRID', $3, $4, $5, $6, 'ACTIVE', $7)
        RETURNING id
        """,
        name, description or None,
        base_row["id"], quote_row["id"],
        venue_id, taker_fee, json.dumps(meta),
    )
    success(f"Strategy created with id={sid}")


async def edit_strategy_interactive(conn: asyncpg.Connection) -> None:
    header("Edit Strategy")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        "SELECT * FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        error(f"Strategy {sid} not found.")
        return
    r    = dict(row)
    meta = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}")

    print(f"\n  Editing: {BOLD}{r['name']}{RESET}")
    print(f"  Press Enter to keep current value.\n")

    print(f"  {BOLD}Basic fields:{RESET}")
    name        = prompt("Name", r["name"])
    description = prompt("Description", r["description"] or "")
    taker_fee   = prompt_float("Taker fee %", float(r["taker_fee_pct"]))

    print(f"\n  {BOLD}Parameters (metadata):{RESET}")
    for k, current in meta.items():
        if k == "weights":
            raw = prompt(f"  {k}", ",".join(str(w) for w in current))
            try:
                meta[k] = [int(x.strip()) for x in raw.split(",")]
            except ValueError:
                meta[k] = current
        elif isinstance(current, bool):
            meta[k] = prompt_bool(f"  {k}", current)
        elif isinstance(current, int):
            meta[k] = prompt_int(f"  {k}", current)
        elif isinstance(current, float):
            meta[k] = prompt_float(f"  {k}", current)

    if not prompt_bool("Save changes?"):
        warn("Cancelled.")
        return

    await conn.execute(
        """
        UPDATE inotives_tradings.trade_strategies
        SET name=$1, description=$2, taker_fee_pct=$3, metadata=$4, updated_at=NOW()
        WHERE id=$5
        """,
        name, description or None, taker_fee, json.dumps(meta), sid,
    )
    success(f"Strategy {sid} updated.")


async def change_strategy_status_interactive(conn: asyncpg.Connection) -> None:
    header("Change Strategy Status")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        "SELECT id, name, status FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        error(f"Strategy {sid} not found.")
        return
    sc = strategy_status_colour(row["status"])
    print(f"\n  {row['name']}  —  current status: {sc}{row['status']}{RESET}\n")

    statuses = ["ACTIVE", "PAUSED", "ARCHIVED"]
    choice   = menu(statuses)
    if choice == "0" or not choice.isdigit() or int(choice) > len(statuses):
        return
    new_status = statuses[int(choice) - 1]

    if new_status == row["status"]:
        warn("Status unchanged.")
        return
    if not prompt_bool(f"Set status to {new_status}?"):
        warn("Cancelled.")
        return

    await _set_strategy_status(conn, sid, new_status)
    success(f"Strategy {sid} → {new_status}")


async def delete_strategy_interactive(conn: asyncpg.Connection) -> None:
    header("Delete Strategy (soft)")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return

    row = await conn.fetchrow(
        "SELECT id, name FROM inotives_tradings.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        error(f"Strategy {sid} not found.")
        return

    print(f"\n  {RED}This will soft-delete strategy: {BOLD}{row['name']}{RESET}")
    if not prompt_bool("Confirm delete?", default=False):
        warn("Cancelled.")
        return

    ok, msg = await _delete_strategy(conn, sid)
    if ok:
        success(msg)
    else:
        error(msg)


async def strategies_menu_interactive(conn: asyncpg.Connection) -> None:
    while True:
        header("Manage Strategies")
        choice = menu([
            "List strategies",
            "View strategy details",
            "Create strategy",
            "Edit strategy parameters",
            "Change strategy status",
            "Delete strategy",
        ])
        if   choice == "0": break
        elif choice == "1": await show_strategies_interactive(conn)
        elif choice == "2": await view_strategy_interactive(conn)
        elif choice == "3": await create_strategy_interactive(conn)
        elif choice == "4": await edit_strategy_interactive(conn)
        elif choice == "5": await change_strategy_status_interactive(conn)
        elif choice == "6": await delete_strategy_interactive(conn)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


async def list_cycles_interactive(conn: asyncpg.Connection) -> None:
    header("List Cycles")
    rows = await _fetch_strategies(conn)
    if not rows:
        warn("No strategies found.")
        return
    print(f"\n  {BOLD}{'ID':<5} {'Name':<30} Status{RESET}")
    for r in rows:
        sc = strategy_status_colour(r["status"])
        print(f"  {r['id']:<5} {r['name']:<30} {sc}{r['status']}{RESET}")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return

    cycles = await _fetch_cycles(conn, sid)
    if not cycles:
        warn("No cycles found.")
        return

    print(f"\n  {BOLD}{'ID':<6} {'#':<4} {'Status':<10} {'Capital':>10} {'Opened':<18} {'Closed':<18} {'Trigger':<16} {'Regime'}{RESET}")
    print(f"  {DIM}{'─'*100}{RESET}")
    for r in cycles:
        sc       = cycle_status_colour(r["status"])
        opened   = r["opened_at"].strftime("%Y-%m-%d %H:%M") if r["opened_at"] else "—"
        closed   = r["closed_at"].strftime("%Y-%m-%d %H:%M") if r["closed_at"] else "—"
        trigger  = r["close_trigger"] or "—"
        regime   = r["volatility_regime"] or "—"
        capital  = f"${float(r['capital_allocated']):,.2f}"
        print(
            f"  {r['id']:<6} {r['cycle_number']:<4} {sc}{r['status']:<10}{RESET} "
            f"{capital:>10}  {opened:<18} {closed:<18} {trigger:<16} {regime}"
        )


async def view_cycle_interactive(conn: asyncpg.Connection) -> None:
    header("View Cycle Details")
    cid = prompt_int("Cycle ID")
    if cid is None:
        return

    cycle = await _fetch_cycle_detail(conn, cid)
    if not cycle:
        error(f"Cycle {cid} not found.")
        return
    sc = cycle_status_colour(cycle["status"])

    dd = await conn.fetchrow(
        "SELECT * FROM inotives_tradings.trade_dca_cycle_details WHERE cycle_id=$1", cid
    )

    print(f"\n  {BOLD}Cycle #{cycle['cycle_number']}{RESET}  —  {cycle['strategy_name']}")
    print(f"  {BOLD}ID:{RESET}             {cycle['id']}")
    print(f"  {BOLD}Pair:{RESET}           {cycle['base_asset'].upper()}/{cycle['quote_asset'].upper()}")
    print(f"  {BOLD}Status:{RESET}         {sc}{cycle['status']}{RESET}")
    print(f"  {BOLD}Capital:{RESET}        ${float(cycle['capital_allocated']):,.2f}")
    if cycle.get("stop_loss_price"):
        print(f"  {BOLD}Stop Loss:{RESET}      {float(cycle['stop_loss_price']):,.2f}")
    else:
        print(f"  {BOLD}Stop Loss:{RESET}      —")
    print(f"  {BOLD}Opened:{RESET}         {cycle['opened_at'].strftime('%Y-%m-%d %H:%M:%S')}")
    if cycle["closed_at"]:
        print(f"  {BOLD}Closed:{RESET}         {cycle['closed_at'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  {BOLD}Close trigger:{RESET}  {cycle['close_trigger']}")

    if dd:
        print(f"\n  {BOLD}DCA Details:{RESET}")
        print(f"    Regime:         {dd['volatility_regime']}")
        print(f"    Grid spacing:   {float(dd['grid_spacing_pct']):.3f}%")
        print(f"    Profit target:  {float(dd['profit_target_pct']):.2f}%")
        print(f"    ATR at open:    {float(dd['atr_at_open']):,.2f}")
        print(f"    Multiplier:     {float(dd['atr_multiplier'])}")
        print(f"    Last tuned:     {dd['last_tuned_at'].strftime('%Y-%m-%d %H:%M')}")

    levels = await conn.fetch(
        """
        SELECT gl.level_num, gl.target_price, gl.quantity, gl.capital_allocated,
               gl.weight, gl.status, gl.filled_at, gl.level_trigger,
               o.avg_fill_price
        FROM inotives_tradings.trade_grid_levels gl
        LEFT JOIN inotives_tradings.trade_orders o ON o.id = gl.order_id
        WHERE gl.cycle_id=$1
        ORDER BY gl.level_num
        """,
        cid,
    )
    if levels:
        print(f"\n  {BOLD}Grid Levels:{RESET}")
        print(f"  {BOLD}  {'Lvl':<4} {'Target':>12} {'Qty':>14} {'Capital':>10} {'Wt':>4} {'Fill Price':>12} {'Status':<10} Trigger{RESET}")
        print(f"  {DIM}  {'─'*85}{RESET}")
        for lv in levels:
            ls       = level_status_colour(lv["status"])
            fill_px  = f"{float(lv['avg_fill_price']):,.2f}" if lv["avg_fill_price"] else "—"
            target   = f"{float(lv['target_price']):,.2f}"
            qty      = f"{float(lv['quantity']):.6f}"
            capital  = f"${float(lv['capital_allocated']):,.2f}"
            trigger  = lv["level_trigger"] or "initial"
            print(
                f"    {lv['level_num']:<4} {target:>12} {qty:>14} {capital:>10} "
                f"{float(lv['weight']):>4.1f} {fill_px:>12} {ls}{lv['status']:<10}{RESET} {trigger}"
            )

    pnl = await conn.fetchrow("SELECT * FROM inotives_tradings.trade_pnl WHERE cycle_id=$1", cid)
    if pnl:
        pnl_colour = GREEN if float(pnl["net_pnl"]) >= 0 else RED
        print(f"\n  {BOLD}P&L:{RESET}")
        print(f"    Gross P&L:  {pnl_colour}${float(pnl['gross_pnl']):,.4f}{RESET}")
        print(f"    Fees:       ${float(pnl['total_fees']):,.4f}")
        print(f"    Net P&L:    {pnl_colour}${float(pnl['net_pnl']):,.4f}  ({float(pnl['pnl_pct']):.3f}%){RESET}")


async def cancel_cycle_interactive(conn: asyncpg.Connection) -> None:
    header("Cancel / Force-Close Cycle")
    cid = prompt_int("Cycle ID")
    if cid is None:
        return

    cycle = await conn.fetchrow(
        "SELECT id, cycle_number, status FROM inotives_tradings.trade_cycles WHERE id=$1 AND deleted_at IS NULL",
        cid,
    )
    if not cycle:
        error(f"Cycle {cid} not found.")
        return
    if cycle["status"] not in ("OPEN", "CLOSING"):
        warn(f"Cycle is already {cycle['status']} — nothing to do.")
        return

    print(f"\n  Cycle #{cycle['cycle_number']}  —  status: {cycle['status']}")
    print(f"\n  Action:")
    choice = menu(["Cancel (no fill, CANCELLED status)", "Force-close (mark CLOSED with manual trigger)"])
    if choice == "0":
        return

    if not prompt_bool("Confirm?", default=False):
        warn("Cancelled.")
        return

    action = "cancel" if choice == "1" else "close"
    ok, msg = await _close_or_cancel_cycle(conn, cid, action)
    if ok:
        success(msg)
    else:
        error(msg)


async def cycles_menu_interactive(conn: asyncpg.Connection) -> None:
    while True:
        header("Manage Cycles")
        choice = menu([
            "List cycles for a strategy",
            "View cycle details + grid levels",
            "Cancel / force-close a cycle",
        ])
        if   choice == "0": break
        elif choice == "1": await list_cycles_interactive(conn)
        elif choice == "2": await view_cycle_interactive(conn)
        elif choice == "3": await cancel_cycle_interactive(conn)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


async def run_interactive() -> int:
    print(f"\n{BOLD}{CYAN}  ╔══════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}  ║     Trade Management CLI             ║{RESET}")
    print(f"{BOLD}{CYAN}  ╚══════════════════════════════════════╝{RESET}")

    try:
        conn = await connect()
    except Exception as exc:
        error(f"Cannot connect to DB: {exc}")
        return 1

    try:
        while True:
            header("Main Menu")
            choice = menu(["Manage Strategies", "Manage Cycles"])
            if   choice == "0": break
            elif choice == "1": await strategies_menu_interactive(conn)
            elif choice == "2": await cycles_menu_interactive(conn)
    finally:
        await conn.close()

    print(f"\n  {DIM}Bye.{RESET}\n")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  CLI PARSER
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manage trade strategies and cycles (interactive or scripted).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Non-interactive examples:
  %(prog)s list-strategies
  %(prog)s list-strategies --json
  %(prog)s view-strategy   --strategy-id 5
  %(prog)s activate        --strategy-id 5
  %(prog)s pause           --strategy-id 5
  %(prog)s archive         --strategy-id 5
  %(prog)s update          --strategy-id 5 --param capital_per_cycle=2000
  %(prog)s update          --strategy-id 5 --param force_entry=true --param num_levels=7
  %(prog)s delete-strategy --strategy-id 5
  %(prog)s list-cycles     --strategy-id 5
  %(prog)s list-cycles     --strategy-id 5 --json
  %(prog)s view-cycle      --cycle-id 10
  %(prog)s cancel-cycle    --cycle-id 10
  %(prog)s close-cycle     --cycle-id 10

Interactive mode (no arguments):
  %(prog)s
  %(prog)s interactive
        """,
    )
    sub = p.add_subparsers(dest="command")

    # ── interactive ───────────────────────────────────────────────────────────
    sub.add_parser("interactive", help="Launch interactive menu (default when no args)")

    # ── list-strategies ───────────────────────────────────────────────────────
    sp = sub.add_parser("list-strategies", help="List all strategies")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # ── view-strategy ─────────────────────────────────────────────────────────
    sp = sub.add_parser("view-strategy", help="View strategy details")
    sp.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # ── activate / pause / archive ────────────────────────────────────────────
    for cmd, help_text in [
        ("activate", "Set strategy status to ACTIVE"),
        ("pause",    "Set strategy status to PAUSED"),
        ("archive",  "Set strategy status to ARCHIVED"),
    ]:
        sp = sub.add_parser(cmd, help=help_text)
        sp.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")

    # ── update ────────────────────────────────────────────────────────────────
    sp = sub.add_parser("update", help="Update strategy metadata parameters")
    sp.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")
    sp.add_argument("--param", action="append", required=True,
                    help="key=value pair (repeatable). Values are JSON-parsed.")

    # ── delete-strategy ───────────────────────────────────────────────────────
    sp = sub.add_parser("delete-strategy", help="Soft-delete a strategy")
    sp.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")

    # ── list-cycles ───────────────────────────────────────────────────────────
    sp = sub.add_parser("list-cycles", help="List cycles for a strategy")
    sp.add_argument("--strategy-id", type=int, required=True, help="Strategy ID")
    sp.add_argument("--limit", type=int, default=30, help="Max rows (default: 30)")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # ── view-cycle ────────────────────────────────────────────────────────────
    sp = sub.add_parser("view-cycle", help="View cycle details with grid levels")
    sp.add_argument("--cycle-id", type=int, required=True, help="Cycle ID")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # ── cancel-cycle ──────────────────────────────────────────────────────────
    sp = sub.add_parser("cancel-cycle", help="Cancel an open cycle (CANCELLED status)")
    sp.add_argument("--cycle-id", type=int, required=True, help="Cycle ID")

    # ── close-cycle ───────────────────────────────────────────────────────────
    sp = sub.add_parser("close-cycle", help="Force-close an open cycle (CLOSED status)")
    sp.add_argument("--cycle-id", type=int, required=True, help="Cycle ID")

    return p


async def dispatch(args) -> int:
    cmd = args.command
    if cmd is None or cmd == "interactive":
        return await run_interactive()
    elif cmd == "list-strategies":
        return await cmd_list_strategies(args)
    elif cmd == "view-strategy":
        return await cmd_view_strategy(args)
    elif cmd in ("activate", "pause", "archive"):
        return await cmd_set_status(args)
    elif cmd == "update":
        return await cmd_update(args)
    elif cmd == "delete-strategy":
        return await cmd_delete_strategy(args)
    elif cmd == "list-cycles":
        return await cmd_list_cycles(args)
    elif cmd == "view-cycle":
        return await cmd_view_cycle(args)
    elif cmd == "cancel-cycle":
        return await cmd_cancel_cycle(args)
    elif cmd == "close-cycle":
        return await cmd_close_cycle(args)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(dispatch(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
