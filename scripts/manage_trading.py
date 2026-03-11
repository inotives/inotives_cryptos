"""
Interactive CLI for managing trade strategies and trade cycles.

Usage:
    uv run --env-file configs/envs/.env.local python scripts/manage_trading.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

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
    return await asyncpg.connect(
        host     = os.environ.get("DB_HOST", "localhost"),
        port     = int(os.environ.get("DB_PORT", 5432)),
        user     = os.environ["DB_USER"],
        password = os.environ["DB_PASSWORD"],
        database = os.environ["DB_NAME"],
    )


# ── Input helpers ─────────────────────────────────────────────────────────────

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
#  STRATEGIES
# ══════════════════════════════════════════════════════════════════════════════

async def list_strategies(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT ts.id, ts.name, ts.strategy_type, ts.status,
               ts.taker_fee_pct, ts.created_at,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset,
               v.name       AS venue
        FROM base.trade_strategies ts
        JOIN base.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN base.assets a_quote ON a_quote.id = ts.quote_asset_id
        JOIN base.venues v       ON v.id        = ts.venue_id
        WHERE ts.deleted_at IS NULL
        ORDER BY ts.id
        """
    )
    return [dict(r) for r in rows]


async def show_strategies(conn: asyncpg.Connection) -> None:
    header("Strategies")
    rows = await list_strategies(conn)
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


async def view_strategy(conn: asyncpg.Connection) -> None:
    header("View Strategy")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        """
        SELECT ts.*,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset,
               v.name       AS venue_name
        FROM base.trade_strategies ts
        JOIN base.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN base.assets a_quote ON a_quote.id = ts.quote_asset_id
        JOIN base.venues v       ON v.id        = ts.venue_id
        WHERE ts.id = $1 AND ts.deleted_at IS NULL
        """,
        sid,
    )
    if not row:
        error(f"Strategy {sid} not found.")
        return
    r = dict(row)
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

    # Cycle summary
    counts = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status='OPEN')      AS open_count,
            COUNT(*) FILTER (WHERE status='CLOSED')    AS closed_count,
            COUNT(*) FILTER (WHERE status='CANCELLED') AS cancelled_count
        FROM base.trade_cycles WHERE strategy_id=$1 AND deleted_at IS NULL
        """,
        sid,
    )
    print(f"\n  {BOLD}Cycles:{RESET}      "
          f"{GREEN}open={counts['open_count']}{RESET}  "
          f"{DIM}closed={counts['closed_count']}  cancelled={counts['cancelled_count']}{RESET}")


async def create_strategy(conn: asyncpg.Connection) -> None:
    header("Create Strategy")

    # Venue selection
    venues = await conn.fetch("SELECT id, name, venue_type FROM base.venues WHERE deleted_at IS NULL ORDER BY id")
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

    # Asset selection
    base_code  = prompt("Base asset code (e.g. btc)").lower()
    quote_code = prompt("Quote asset code (e.g. usdt)").lower()
    base_row  = await conn.fetchrow("SELECT id FROM base.assets WHERE code=$1 AND deleted_at IS NULL", base_code)
    quote_row = await conn.fetchrow("SELECT id FROM base.assets WHERE code=$1 AND deleted_at IS NULL", quote_code)
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
        INSERT INTO base.trade_strategies
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


async def edit_strategy(conn: asyncpg.Connection) -> None:
    header("Edit Strategy")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        "SELECT * FROM base.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
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
        UPDATE base.trade_strategies
        SET name=$1, description=$2, taker_fee_pct=$3, metadata=$4, updated_at=NOW()
        WHERE id=$5
        """,
        name, description or None, taker_fee, json.dumps(meta), sid,
    )
    success(f"Strategy {sid} updated.")


async def change_strategy_status(conn: asyncpg.Connection) -> None:
    header("Change Strategy Status")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        "SELECT id, name, status FROM base.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
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

    await conn.execute(
        "UPDATE base.trade_strategies SET status=$1::base.trade_strategy_status, updated_at=NOW() WHERE id=$2",
        new_status, sid,
    )
    success(f"Strategy {sid} → {new_status}")


async def delete_strategy(conn: asyncpg.Connection) -> None:
    header("Delete Strategy (soft)")
    sid = prompt_int("Strategy ID")
    if sid is None:
        return
    row = await conn.fetchrow(
        "SELECT id, name, status FROM base.trade_strategies WHERE id=$1 AND deleted_at IS NULL", sid
    )
    if not row:
        error(f"Strategy {sid} not found.")
        return

    open_cycles = await conn.fetchval(
        "SELECT COUNT(*) FROM base.trade_cycles WHERE strategy_id=$1 AND status='OPEN' AND deleted_at IS NULL",
        sid,
    )
    if open_cycles > 0:
        error(f"Strategy has {open_cycles} open cycle(s). Close or cancel them first.")
        return

    print(f"\n  {RED}This will soft-delete strategy: {BOLD}{row['name']}{RESET}")
    if not prompt_bool("Confirm delete?", default=False):
        warn("Cancelled.")
        return

    # Soft delete is handled by trigger on DELETE
    await conn.execute("DELETE FROM base.trade_strategies WHERE id=$1", sid)
    success(f"Strategy {sid} deleted.")


async def strategies_menu(conn: asyncpg.Connection) -> None:
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
        elif choice == "1": await show_strategies(conn)
        elif choice == "2": await view_strategy(conn)
        elif choice == "3": await create_strategy(conn)
        elif choice == "4": await edit_strategy(conn)
        elif choice == "5": await change_strategy_status(conn)
        elif choice == "6": await delete_strategy(conn)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
#  CYCLES
# ══════════════════════════════════════════════════════════════════════════════

async def pick_strategy(conn: asyncpg.Connection) -> int | None:
    rows = await list_strategies(conn)
    if not rows:
        warn("No strategies found.")
        return None
    print(f"\n  {BOLD}{'ID':<5} {'Name':<30} Status{RESET}")
    for r in rows:
        sc = strategy_status_colour(r["status"])
        print(f"  {r['id']:<5} {r['name']:<30} {sc}{r['status']}{RESET}")
    return prompt_int("Strategy ID")


async def list_cycles(conn: asyncpg.Connection) -> None:
    header("List Cycles")
    sid = await pick_strategy(conn)
    if sid is None:
        return

    rows = await conn.fetch(
        """
        SELECT tc.id, tc.cycle_number, tc.status, tc.capital_allocated,
               tc.close_trigger, tc.opened_at, tc.closed_at, tc.stop_loss_price,
               dd.profit_target_pct, dd.volatility_regime, dd.grid_spacing_pct
        FROM base.trade_cycles tc
        LEFT JOIN base.trade_dca_cycle_details dd ON dd.cycle_id = tc.id
        WHERE tc.strategy_id=$1 AND tc.deleted_at IS NULL
        ORDER BY tc.cycle_number DESC
        LIMIT 30
        """,
        sid,
    )
    if not rows:
        warn("No cycles found.")
        return

    print(f"\n  {BOLD}{'ID':<6} {'#':<4} {'Status':<10} {'Capital':>10} {'Opened':<18} {'Closed':<18} {'Trigger':<16} {'Regime'}{RESET}")
    print(f"  {DIM}{'─'*100}{RESET}")
    for r in rows:
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


async def view_cycle(conn: asyncpg.Connection) -> None:
    header("View Cycle Details")
    cid = prompt_int("Cycle ID")
    if cid is None:
        return

    cycle = await conn.fetchrow(
        """
        SELECT tc.*,
               ts.name AS strategy_name,
               a_base.code  AS base_asset,
               a_quote.code AS quote_asset
        FROM base.trade_cycles tc
        JOIN base.trade_strategies ts ON ts.id = tc.strategy_id
        JOIN base.assets a_base  ON a_base.id  = ts.base_asset_id
        JOIN base.assets a_quote ON a_quote.id = ts.quote_asset_id
        WHERE tc.id=$1 AND tc.deleted_at IS NULL
        """,
        cid,
    )
    if not cycle:
        error(f"Cycle {cid} not found.")
        return
    c_row = dict(cycle)
    sc    = cycle_status_colour(c_row["status"])

    dd = await conn.fetchrow(
        "SELECT * FROM base.trade_dca_cycle_details WHERE cycle_id=$1", cid
    )

    print(f"\n  {BOLD}Cycle #{c_row['cycle_number']}{RESET}  —  {c_row['strategy_name']}")
    print(f"  {BOLD}ID:{RESET}             {c_row['id']}")
    print(f"  {BOLD}Pair:{RESET}           {c_row['base_asset'].upper()}/{c_row['quote_asset'].upper()}")
    print(f"  {BOLD}Status:{RESET}         {sc}{c_row['status']}{RESET}")
    print(f"  {BOLD}Capital:{RESET}        ${float(c_row['capital_allocated']):,.2f}")
    print(f"  {BOLD}Stop Loss:{RESET}      {float(c_row['stop_loss_price']):,.2f}" if c_row.get("stop_loss_price") else f"  {BOLD}Stop Loss:{RESET}      —")
    print(f"  {BOLD}Opened:{RESET}         {c_row['opened_at'].strftime('%Y-%m-%d %H:%M:%S')}")
    if c_row["closed_at"]:
        print(f"  {BOLD}Closed:{RESET}         {c_row['closed_at'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  {BOLD}Close trigger:{RESET}  {c_row['close_trigger']}")

    if dd:
        print(f"\n  {BOLD}DCA Details:{RESET}")
        print(f"    Regime:         {dd['volatility_regime']}")
        print(f"    Grid spacing:   {float(dd['grid_spacing_pct']):.3f}%")
        print(f"    Profit target:  {float(dd['profit_target_pct']):.2f}%")
        print(f"    ATR at open:    {float(dd['atr_at_open']):,.2f}")
        print(f"    Multiplier:     {float(dd['atr_multiplier'])}")
        print(f"    Last tuned:     {dd['last_tuned_at'].strftime('%Y-%m-%d %H:%M')}")

    # Grid levels
    levels = await conn.fetch(
        """
        SELECT gl.level_num, gl.target_price, gl.quantity, gl.capital_allocated,
               gl.weight, gl.status, gl.filled_at, gl.level_trigger,
               o.avg_fill_price
        FROM base.trade_grid_levels gl
        LEFT JOIN base.trade_orders o ON o.id = gl.order_id
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

    # PnL (if closed)
    pnl = await conn.fetchrow("SELECT * FROM base.trade_pnl WHERE cycle_id=$1", cid)
    if pnl:
        pnl_colour = GREEN if float(pnl["net_pnl"]) >= 0 else RED
        print(f"\n  {BOLD}P&L:{RESET}")
        print(f"    Gross P&L:  {pnl_colour}${float(pnl['gross_pnl']):,.4f}{RESET}")
        print(f"    Fees:       ${float(pnl['total_fees']):,.4f}")
        print(f"    Net P&L:    {pnl_colour}${float(pnl['net_pnl']):,.4f}  ({float(pnl['pnl_pct']):.3f}%){RESET}")


async def cancel_cycle(conn: asyncpg.Connection) -> None:
    header("Cancel / Force-Close Cycle")
    cid = prompt_int("Cycle ID")
    if cid is None:
        return

    cycle = await conn.fetchrow(
        "SELECT id, cycle_number, status, strategy_id FROM base.trade_cycles WHERE id=$1 AND deleted_at IS NULL",
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

    now = datetime.now(timezone.utc)

    if choice == "1":
        # Cancel all pending/open grid levels
        await conn.execute(
            """
            UPDATE base.trade_grid_levels
            SET status='CANCELLED', updated_at=NOW()
            WHERE cycle_id=$1 AND status IN ('PENDING','OPEN')
            """,
            cid,
        )
        await conn.execute(
            """
            UPDATE base.trade_cycles
            SET status='CANCELLED', close_trigger='manual', closed_at=$1, updated_at=NOW()
            WHERE id=$2
            """,
            now, cid,
        )
        success(f"Cycle {cid} cancelled.")

    elif choice == "2":
        await conn.execute(
            """
            UPDATE base.trade_grid_levels
            SET status='CANCELLED', updated_at=NOW()
            WHERE cycle_id=$1 AND status IN ('PENDING','OPEN')
            """,
            cid,
        )
        await conn.execute(
            """
            UPDATE base.trade_cycles
            SET status='CLOSED', close_trigger='manual', closed_at=$1, updated_at=NOW()
            WHERE id=$2
            """,
            now, cid,
        )
        success(f"Cycle {cid} force-closed.")


async def cycles_menu(conn: asyncpg.Connection) -> None:
    while True:
        header("Manage Cycles")
        choice = menu([
            "List cycles for a strategy",
            "View cycle details + grid levels",
            "Cancel / force-close a cycle",
        ])
        if   choice == "0": break
        elif choice == "1": await list_cycles(conn)
        elif choice == "2": await view_cycle(conn)
        elif choice == "3": await cancel_cycle(conn)
        input(f"\n  {DIM}Press Enter to continue...{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    print(f"\n{BOLD}{CYAN}  ╔══════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}  ║     Trade Management CLI             ║{RESET}")
    print(f"{BOLD}{CYAN}  ╚══════════════════════════════════════╝{RESET}")

    try:
        conn = await connect()
    except Exception as exc:
        error(f"Cannot connect to DB: {exc}")
        sys.exit(1)

    try:
        while True:
            header("Main Menu")
            choice = menu(["Manage Strategies", "Manage Cycles"])
            if   choice == "0": break
            elif choice == "1": await strategies_menu(conn)
            elif choice == "2": await cycles_menu(conn)
    finally:
        await conn.close()

    print(f"\n  {DIM}Bye.{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
