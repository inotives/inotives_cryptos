# Hybrid DCA Grid Trading Bots — Technical Design

This document describes the full technical design of the Hybrid Grid + Regime-Switching trading system. It covers strategy logic, indicator calculations, capital coordination, intraday safeguards, the data pipeline, and database layout — with worked examples throughout.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Market Regime Detection](#2-market-regime-detection)
3. [DCA Grid Strategy](#3-dca-grid-strategy)
4. [Trend Following Strategy](#4-trend-following-strategy)
5. [Hybrid Capital Coordinator](#5-hybrid-capital-coordinator)
6. [Intraday Safeguards](#6-intraday-safeguards)
7. [Daily Data Pipeline](#7-daily-data-pipeline)
8. [Database Schema](#8-database-schema)
9. [Bot Runtime Architecture](#9-bot-runtime-architecture)
10. [Configuration Reference](#10-configuration-reference)

---

## 1. System Overview

The system runs two complementary strategies simultaneously, switching capital between them based on a daily **Regime Score (RS)**.

```
                         ┌─────────────────────┐
                         │  Data Bot            │
                         │  02:00 UTC daily     │
                         │  OHLCV → Indicators  │
                         │       → Regime Score │
                         └──────────┬──────────┘
                                    │ RS 0–100
                         ┌──────────▼──────────┐
                         │  Hybrid Coordinator  │
                         │  Capital Splitter    │
                         └────┬────────────┬───┘
                              │            │
               Grid_Limit     │            │  Trend_Limit
          = (100-RS)/100 × C  │            │  = RS/100 × C
                              ▼            ▼
                    ┌──────────────┐  ┌──────────────┐
                    │  DCA Grid    │  │ Trend Follow │
                    │  Mean revert │  │  Momentum    │
                    │  RS 0–60     │  │  RS 61–100   │
                    └──────────────┘  └──────────────┘
```

### Strategy Allocation by Regime Score

| RS Range | Market State | Grid Allocation | Trend Allocation |
|---|---|---|---|
| **0 – 30** | Deep Sideways | ~100% | ~0% (idle) |
| **31 – 60** | Hybrid / Transition | Sliding scale | Sliding scale |
| **61 – 100** | Strong Trend | ~0% (paused) | ~100% |

**Key design principles:**

- Capital scaling is applied **at cycle-open time only** — an open cycle always runs to completion with the capital it started with. No mid-cycle resizing.
- When the market transitions from trend → sideways, **trend positions are not hard-closed**. They exit naturally via their trailing stop while the grid begins placing orders.
- If the intraday price deviates drastically from the daily open (circuit breaker), the Regime Score is overridden to 0 for that tick — protecting against flash crash entries.
- Both strategies use **live intraday data** (exchange candles, price observations) to compensate for the 24-hour gap between daily indicator updates.

---

## 2. Market Regime Detection

The Regime Score is a composite 0–100 score computed daily from three normalised components.

### Formula

```
RS = (score_adx × 0.4) + (score_slope × 0.4) + (score_vol × 0.2)
```

Each component is normalised to 0–100 before weighting.

---

### Component 1: ADX(14) — Trend Strength (weight 0.4)

**What it measures:** How strong the current trend is, regardless of direction. ADX does not indicate whether the trend is up or down — only how powerful it is.

**Normalisation (piecewise linear):**

```
ADX ≤ 15  → score = 0    (market is dead, no trend)
ADX = 25  → score = 50   (the conventional "trend threshold")
ADX ≥ 40  → score = 100  (strong, established trend)

Between 15–25:  score = (ADX - 15) / (25 - 15) × 50
Between 25–40:  score = 50 + (ADX - 25) / (40 - 25) × 50
```

**Why 25 as the pivot (not 60)?** Waiting for ADX=60 means you've already missed most of the move. ADX=25 is the standard entry point for trend-following systems.

**Examples:**

| ADX | score_adx | Interpretation |
|---|---|---|
| 10 | 0.0 | Completely flat/choppy |
| 15 | 0.0 | Just at the dead zone boundary |
| 20 | 25.0 | Mild momentum forming |
| 25 | 50.0 | Trend threshold crossed |
| 33 | 76.7 | Well-established trend |
| 40 | 100.0 | Strong trend (capped) |
| 55 | 100.0 | Very strong trend (still capped) |

---

### Component 2: EMA Slope 5d% — Trend Direction & Velocity (weight 0.4)

**What it measures:** How fast EMA(50) is rising over the last 5 days, expressed as a percentage. Captures both direction (positive = uptrend) and velocity (steeper = stronger conviction).

**Formula:**
```
ema_slope_5d = ((EMA50_today - EMA50_5d_ago) / EMA50_5d_ago) × 100
```

**Normalisation (linear):**
```
slope ≤ 0%    → score = 0    (flat or falling — not an uptrend)
slope ≥ 0.5%  → score = 100  (strong upward momentum)
Between 0–0.5%: score = slope / 0.5 × 100
```

**Why 0.5% as the ceiling?** A 0.5%/day move in EMA50 compounds to ~2.5% over 5 days — that's a meaningful, sustained uptrend, not noise.

**Examples:**

| EMA50 today | EMA50 5d ago | Slope% | score_slope | State |
|---|---|---|---|---|
| 70,000 | 72,000 | −2.78% | 0.0 | Downtrend |
| 70,000 | 70,000 | 0.00% | 0.0 | Flat |
| 70,000 | 69,650 | +0.50% | 100.0 | Strong uptrend |
| 70,000 | 69,860 | +0.20% | 40.0 | Mild uptrend |
| 70,000 | 69,300 | +1.01% | 100.0 | Very strong (capped) |

---

### Component 3: Volatility Ratio — Noise Filter (weight 0.2, inverted)

**What it measures:** `ATR(14) / StdDev(Close, 14)` — the ratio of directional range to raw price noise.

- **Low ratio (< 0.8):** Price is moving smoothly and directionally. Each ATR unit is "efficient" — it represents real trend movement, not random oscillation. High confidence in trend.
- **High ratio (> 1.2):** Price is whipping around. ATR is large but it's mostly noise. Mean-reversion likely.

**Normalisation (inverted linear):**
```
ratio ≥ 1.2  → score = 0    (choppy — no trend signal)
ratio ≤ 0.8  → score = 100  (smooth directional move)
Between 0.8–1.2: score = (1.2 - ratio) / (1.2 - 0.8) × 100
```

**Intuition:** Low noise = high confidence in trend direction. Imagine two scenarios where ATR=2000:
- Scenario A: Price is up $2000 for 14 days straight → ratio ≈ 0.6 → score = 100
- Scenario B: Price swings +$2000, -$1800, +$2000, -$1800 → ratio ≈ 1.5 → score = 0

**Examples:**

| Vol Ratio | score_vol | Interpretation |
|---|---|---|
| 0.60 | 100.0 | Very smooth directional move |
| 0.80 | 100.0 | Smooth (at boundary) |
| 1.00 | 50.0 | Neutral |
| 1.20 | 0.0 | Choppy (at boundary) |
| 1.55 | 0.0 | Very choppy (capped) |

---

### Regime Score Worked Examples

**Example A — BTC in a strong bull run (halving rally):**
```
ADX = 45.0   → score_adx   = 100.0
Slope = 5.1% → score_slope = 100.0
VolR = 0.65  → score_vol   = 100.0

RS = 100×0.4 + 100×0.4 + 100×0.2 = 100.0
→ Strong Trend: 100% capital to Trend Following, DCA Grid paused
```

**Example B — BTC peak bull (high momentum, starting to get volatile):**
```
ADX = 35.7   → score_adx   = 50 + (35.7-25)/(40-25) × 50 = 85.7
Slope = 2.5% → score_slope = 100.0  (capped, well above 0.5%)
VolR = 0.77  → score_vol   = (1.2-0.77)/(1.2-0.8) × 100 = 100.0 (capped)

RS = 85.7×0.4 + 100×0.4 + 100×0.2 = 94.3
→ Strong Trend: Trend Following dominates
```

**Example C — BTC correction (downtrend, choppy):**
```
ADX = 33.75  → score_adx   = 50 + (33.75-25)/(40-25) × 50 = 79.2
Slope = −1.5% → score_slope = 0.0  (negative slope → no uptrend)
VolR = 1.55  → score_vol   = 0.0  (very choppy)

RS = 79.2×0.4 + 0×0.4 + 0×0.2 = 31.7
→ DCA Grid gets 68.3% of capital, Trend idles (RS < 61)
```

---

### Where it's stored

```sql
SELECT metric_date, raw_adx, raw_slope, raw_vol_ratio,
       score_adx, score_slope, score_vol, final_regime_score
FROM inotives_tradings.asset_market_regime
WHERE asset_id = 26   -- BTC
ORDER BY metric_date DESC LIMIT 5;
```

---

## 3. DCA Grid Strategy

### What is DCA Grid?

A Dollar-Cost Averaging Grid places multiple **limit buy orders** below the current price, spaced by a fixed percentage. As price drops, orders fill at lower and lower prices, averaging down the entry cost. When price recovers to a target above the average entry, all positions are sold in one take-profit close.

This strategy thrives in **sideways / range-bound markets** where price oscillates without a strong directional trend.

---

### ATR-Based Grid Spacing

The grid is not fixed-percentage — it adapts to the asset's current volatility using **ATR(14)**.

```
grid_spacing_pct = (ATR(14) / current_price × 100) × atr_multiplier
```

The `atr_multiplier` is selected based on the current volatility regime:

| Volatility Regime | ATR Multiplier | Profit Target |
|---|---|---|
| `low` | 0.4 | 1.0% |
| `normal` | 0.5 | 1.5% |
| `high` | 0.7 | 2.5% |
| `extreme` | — | (entry blocked) |

**Example: BTC at $70,000, ATR(14) = $2,100, regime = normal**

```
ATR%             = 2100 / 70000 × 100 = 3.0%
grid_spacing_pct = 3.0% × 0.5         = 1.5%
```

---

### Grid Level Calculation

With `capital_per_cycle = $1,000`, `num_levels = 5`, `weights = [1,1,2,3,3]`:

```
Total weight = 1+1+2+3+3 = 10

Level 1: target = 70000 × (1 - 1×0.015) = $68,950  capital = 1000 × 1/10 = $100
Level 2: target = 70000 × (1 - 2×0.015) = $67,900  capital = 1000 × 1/10 = $100
Level 3: target = 70000 × (1 - 3×0.015) = $66,850  capital = 1000 × 2/10 = $200
Level 4: target = 70000 × (1 - 4×0.015) = $65,800  capital = 1000 × 3/10 = $300
Level 5: target = 70000 × (1 - 5×0.015) = $64,750  capital = 1000 × 3/10 = $300
```

**Fee-adjusted quantities** (Crypto.com maker fee = 0.25%):
```
qty = capital / (target_price × (1 + 0.0025))

Level 1: 100 / (68950 × 1.0025) = 0.001446 BTC
Level 4: 300 / (65800 × 1.0025) = 0.004554 BTC
```

**Stop loss** — placed one spacing below the deepest level:
```
stop_loss = Level5_price × (1 - grid_spacing_pct)
          = 64750 × (1 - 0.015)
          = $63,779
```

---

### Take Profit

Calculated once, when the cycle opens:
```
target_sell = avg_entry_price × (1 + profit_target% + taker_fee%)
```

**Example:** If levels 1–3 filled (prices $68,950 / $67,900 / $66,850):
```
Total BTC = 0.001446 + 0.001469 + 0.002986 = 0.005901 BTC
Total cost = $100 + $100 + $200 = $400

avg_entry = 400 / 0.005901 = $67,786
target_sell = 67786 × (1 + 0.015 + 0.005)
            = 67786 × 1.020
            = $69,142
```

---

### Entry Conditions

All must pass before a new cycle opens:

| Condition | Default | Purpose |
|---|---|---|
| `price > SMA(200)` | `require_uptrend=True` | Avoid entering a sustained bear market |
| `SMA(50) > SMA(200)` | `require_golden_cross=True` | Golden cross confirms medium-term uptrend |
| `RSI(14) < rsi_entry_max` | 60 | Don't buy into an overbought rally |
| `ATR% < max_atr_pct_entry` | 6.0% | Skip during volatility spikes |
| Regime check | RS < 61 | Grid paused if market is trending strongly |
| Trend priority | RS > 50 | Grid defers if trend strategy has an open cycle |

`force_entry: true` bypasses ALL conditions including regime checks — for testing only.

---

### Intraday Volatility Guard

Before filling grid levels, the DCA Grid checks whether the **4-hour price range** exceeds the daily ATR. If so, the market is moving too fast for safe grid placement and new fills are paused.

```
intraday_range = MAX(price) - MIN(price) over last 4 hours
                 (from inotives_tradings.price_observations)

if intraday_range > daily_ATR(14):
    → PAUSE new grid fills this tick
```

**Example: BTC with ATR(14) = $2,100**

```
Last 4 hours: high = $71,500, low = $68,800
intraday_range = 71500 - 68800 = $2,700

$2,700 > $2,100  → PAUSE (market too volatile for grid fills)
```

This prevents the grid from chasing a fast-moving market where all levels could fill in minutes during a sharp dump.

---

### Defensive Grid Mode

When the normal entry conditions fail (downtrend or golden cross absent) but the strategy has `defensive_mode_enabled: true`, the bot checks for a **bounce signal**:

1. Must actually be in a downtrend (`price < SMA200` or `SMA50 < SMA200`)
2. Live intraday RSI (1h candles, Wilder smoothing) < `defensive_rsi_oversold` (default 40)

If both conditions pass, the bot opens a **wider, safer grid**:

```
Defensive overrides:
  atr_multiplier    = 0.8  (wider spacing — more room to breathe)
  profit_target     = 2.5% (higher reward needed to justify downtrend risk)
  num_levels        = 5
  weights           = [1,1,1,1,1] (equal weight — conservative sizing)
```

**Example: BTC at $62,000, SMA200 = $65,000 (downtrend), intraday RSI(1h) = 32**

```
Normal entry: BLOCKED (price < SMA200, golden cross absent)
Defensive check:
  In downtrend? YES (62000 < 65000)
  RSI(1h) = 32 < 40 (oversold threshold)?  YES → bounce signal detected

→ Open defensive grid with wider spacing (ATR × 0.8) and 2.5% profit target
```

---

### Hybrid Coordination Hook

Before opening a cycle, the DCA Grid checks the coordinator:

```python
# 1. Get regime score (may be overridden to 0 by circuit breaker)
regime_score = get_regime_score_with_circuit_breaker(conn, ...)

# 2. RS >= 61 → strong trend, grid fully paused
if regime_score >= 61:
    return   # do nothing this tick

# 3. RS > 50 + trend cycle is open → trend has priority
if await trend_has_priority(conn, asset_id, regime_score):
    return   # defer to trend strategy

# 4. Scale capital by regime
capital_per_cycle = capital_per_cycle × (100 - RS) / 100
```

**Example at RS = 45, configured capital = $1,000:**
```
Grid capital = 1000 × (100 - 45) / 100 = $550
(Trend capital = 500 × 45/100 = $225, but trend won't enter since RS < 61)
```

---

## 4. Trend Following Strategy

### What is Trend Following?

Trend following enters a single long position when the market breaks out to a new high with a confirmed uptrend structure, then rides the move with a trailing stop that only moves upward. The goal is to capture a large portion of a sustained bull run.

This strategy is active when **RS ≥ 61** (strong trending market).

---

### Entry Conditions

All six must pass, plus an intraday RSI guard:

| # | Condition | Default | Rationale |
|---|---|---|---|
| 1 | `RS >= min_regime_score` | 61.0 | Only enter during confirmed trends |
| 2 | `EMA50 > EMA200` (golden cross) | required | Sustained uptrend structure |
| 3 | `price > 5-day high` | required | Breakout confirmation — price is making new highs |
| 4 | `ADX(14) >= min_adx` | 25.0 | Trend has strength, not just a spike |
| 5 | `RSI(14) < rsi_entry_max` | 70.0 | Not entering at overbought extreme |
| 6 | `ATR% < max_atr_pct_entry` | 6.0% | Not entering during a volatility spike |
| 7 | Intraday RSI(1h) < `rsi_entry_max` | 70.0 | Live confirmation — not overbought right now |

The intraday RSI guard (condition 7) runs **after** conditions 1–6 pass. It fetches live 1-hour candles from the exchange and computes RSI using Wilder's smoothing. This catches cases where the daily RSI looked fine at 02:00 UTC but the market has since rallied into overbought territory.

**Example: BTC at $72,000, 5-day high = $71,500**

```
RS     = 74.3   ≥ 61   ✓
EMA50  = 68,000 > EMA200 = 55,000  ✓
Price  = 72,000 > 5d_high = 71,500  ✓
ADX    = 38     ≥ 25   ✓
RSI    = 62     < 70   ✓  (daily)
ATR%   = 2.8%   < 6.0% ✓
RSI(1h) = 58    < 70   ✓  (intraday — live from exchange)
→ All conditions pass — ENTER
```

**Example: Entry blocked by intraday RSI:**

```
Daily conditions 1–6: ALL PASS (RSI daily = 65)
RSI(1h) = 73  ≥ 70  ✗  (intraday overbought)
→ SKIP entry — market rallied since last daily update
```

---

### Position Sizing

The position size is ATR-scaled to risk a fixed percentage of capital:

```
capital_at_risk = capital_allocated × risk_pct_per_trade
position_size   = capital_at_risk / (ATR × atr_stop_multiplier)
```

Capped at: `capital_allocated / current_price` (never deploy more than allocated).

**Example: capital_allocated = $500, risk = 1%, ATR = $2,100, stop_mult = 2.0:**

```
capital_at_risk  = 500 × 0.01 = $5
position_size    = 5 / (2100 × 2.0) = 0.001190 BTC

Cap check: 500 / 72000 = 0.006944 BTC  → not hit
Fee adjustment (taker 0.5%): 0.001190 / 1.005 = 0.001184 BTC
```

This means if the stop loss fires, the maximum loss is approximately $5 (1% of capital).

---

### Initial Stop Loss & Trailing Stop

**Initial stop loss** (set at entry, never moves down):
```
initial_stop = entry_price - (atr_stop_multiplier × ATR)

Example: 72,000 - (2.0 × 2,100) = $67,800
```

**Trailing stop** (moves up as price rises, uses live intraday ATR):
```
trailing_stop = highest_price_since_entry - (atr_trail_multiplier × ATR)
```

The trailing stop prefers **live intraday ATR** computed from exchange 1-hour candles (29 candles, Wilder's smoothing). If the exchange call fails or returns insufficient data, it falls back to the daily ATR from `inotives_tradings.asset_indicators_1d`.

**Effective stop** (the one that actually triggers):
```
effective_stop = MAX(initial_stop, trailing_stop)
```

The effective stop **only moves upward** — it locks in profit as the trade goes in your favour.

**Walk-through example (entry at $72,000, ATR = $2,100):**

```
Entry: initial_stop = 72000 - (2.0 × 2100) = $67,800

Day 1: price = $74,500, intraday ATR = $2,050
  trailing_stop = 74500 - (3.0 × 2050) = $68,350
  effective_stop = MAX(67800, 68350) = $68,350  ↑ stop moved up

Day 5: price = $81,000, intraday ATR = $1,900 (volatility cooling)
  trailing_stop = 81000 - (3.0 × 1900) = $75,300
  effective_stop = MAX(67800, 75300) = $75,300  ↑ significant lock-in

Day 8: price drops to $74,800
  effective_stop = $75,300 → TRIGGERED
  Exit at $74,800

  PnL = (74800 - 72000) × 0.001184 = $3.31
  Return = (74800 - 72000) / 72000 × 100 = +3.89%
```

Notice: the trailing stop used live ATR that tightened as volatility decreased (from $2,100 at entry to $1,900 by day 5). This locked in more profit than using the stale daily ATR would have.

---

### Cycle State

All position state is stored in `trade_cycles.metadata` (JSONB):

```json
{
    "entry_price":               72000.0,
    "position_size":             0.001184,
    "atr_at_entry":              2100.0,
    "initial_stop_loss":         67800.0,
    "highest_price_since_entry": 81000.0,
    "high_5d_at_entry":          71500.0,
    "entry_order_id":            "ccxt-order-abc123"
}
```

The `stop_loss_price` column on `trade_cycles` is also updated each tick so dashboards always show the current effective stop.

---

### Hybrid Coordination Hook

```python
# 1. Circuit-breaker-aware regime score
regime_score_raw = get_regime_score_with_circuit_breaker(conn, ...)

# 2. RS ≤ 50 + active DCA Grid cycle → grid has priority, trend defers
if regime_score_raw <= 50 and await grid_has_active_cycle(conn, asset_id):
    return   # do not open trend cycle while grid has capital priority

# 3. Scale capital by regime (trend gets RS% of configured allocation)
capital_allocated = capital_allocated × RS / 100
```

**Example at RS = 74, configured capital = $500:**
```
Trend capital = 500 × 74/100 = $370
Grid capital  = 1000 × (100-74)/100 = $260  (grid paused since RS > 61)
```

---

## 5. Hybrid Capital Coordinator

### Capital Split Formula

```
Grid_Limit  = configured_capital × (100 - RS) / 100
Trend_Limit = configured_capital × RS / 100
```

**Scale table (grid configured at $1,000, trend at $500):**

| RS | Grid Capital | Trend Capital | Grid Status | Trend Status |
|---|---|---|---|---|
| 0 | $1,000 | $0 | Full size | Idle |
| 20 | $800 | $100 | Large | Idle |
| 30 | $700 | $150 | Large | Idle |
| 50 | $500 | $250 | Half size | Idle |
| 61 | $390 | $305 | **Paused** | Active |
| 80 | $200 | $400 | Paused | Active |
| 100 | $0 | $500 | Paused | Full size |

Note: Trend idles until RS ≥ 61 (`min_regime_score`). Grid pauses at RS ≥ 61 (`REGIME_GRID_PAUSE`).

---

### Priority Rules

**When RS > 50 (trend has priority):**

If a TREND_FOLLOW cycle is currently OPEN for the asset, the DCA Grid will not open a new cycle. The grid defers until the trend position exits via its trailing stop.

```
RS = 55  →  trend has priority
  Is TREND_FOLLOW cycle OPEN? YES → Grid skips entry this tick
  Is TREND_FOLLOW cycle OPEN? NO  → Grid may enter at scaled capital
```

**When RS ≤ 50 (grid has priority):**

If a DCA_GRID cycle is OPEN, the Trend Following strategy will not enter a new position. It waits for the grid to close naturally.

```
RS = 40  →  grid has priority
  Is DCA_GRID cycle OPEN? YES → Trend skips entry this tick
  Is DCA_GRID cycle OPEN? NO  → Trend may enter if RS ≥ 61 (likely won't at RS=40)
```

---

### Transition Rule (Trend → Grid)

When RS drops from 70 → 35 (trend regime collapses):

1. **Do NOT force-close the open trend position.** It still has a trailing stop protecting profits.
2. **Stop opening new trend cycles.**
3. **Allow the DCA Grid to start placing orders** (at reduced capital since RS > 30 still).
4. The trend position exits naturally when its trailing stop is triggered.

This avoids selling at the worst moment (when the regime score drops due to a pullback that is still within a larger uptrend).

---

## 6. Intraday Safeguards

The regime score and technical indicators are computed once per day at 02:00 UTC. Between pipeline runs, up to 24 hours of market movement can make daily data stale. Four intraday safeguards compensate for this gap.

### 6.1 Circuit Breaker (Hybrid Coordinator)

Protects against opening new cycles during extreme intraday moves.

**Data source:** Today's opening price is the **first `price_observations` row** of the current UTC day (written by the pricing bot). Falls back to the latest daily bar from `asset_metrics_1d` only if no intraday observations exist yet.

```
daily_open    = first price_observations of today (or fallback: asset_metrics_1d.open_price)
current_price = latest price_observations
atr_14        = from asset_indicators_1d

deviation = |current_price - daily_open|
threshold = 2.0 × ATR(14)

if deviation > threshold:
    CIRCUIT BREAKER ACTIVE → RS overridden to 0.0
```

**Example: BTC daily open $70,000 (from first pricing bot tick), ATR = $2,100**

```
threshold = 2.0 × 2100 = $4,200

At 14:00 UTC, price drops to $65,300:
  deviation = |65300 - 70000| = $4,700 > $4,200  → TRIGGERED

Effect: RS forced to 0.0 for this tick
  → DCA Grid won't enter (regime check blocks at RS=0)
  → Trend won't enter (RS 0 < 61)
  → Existing open cycles are NOT affected (their own stops handle that)
```

### 6.2 Live Intraday ATR (Trend Following)

The trailing stop uses **live ATR** computed from exchange 1-hour candles instead of the stale daily ATR.

- Fetches 29 most recent 1h candles via `exchange.fetch_ohlcv(symbol, "1h", limit=29)`
- Computes ATR(14) using Wilder's smoothing (same method as daily pipeline)
- Falls back to daily ATR if the exchange call fails or returns < 15 candles

**Why this matters:** If volatility contracts intraday, the live ATR tightens the trailing stop — locking in more profit. If volatility expands, the stop widens — avoiding premature exit on a noisy pullback.

### 6.3 Intraday RSI Guard (Trend Following)

Blocks new trend entries when the market is overbought **right now**, even if the daily RSI looked fine at 02:00 UTC.

- Fetches 29 most recent 1h candles via `exchange.fetch_ohlcv(symbol, "1h", limit=29)`
- Computes RSI(14) using Wilder's smoothing
- If `RSI(1h) >= rsi_entry_max` (default 70), entry is skipped

### 6.4 Intraday Volatility Guard (DCA Grid)

Pauses new grid level fills when the 4-hour price range exceeds the daily ATR.

- Queries `MAX(price) - MIN(price)` from `price_observations` over the last 4 hours
- Compares against daily ATR(14)
- If `intraday_range > daily_ATR`, fills are paused for that tick

**Summary table:**

| Guard | Source | Used by | Trigger |
|---|---|---|---|
| Circuit breaker | `price_observations` daily open vs current | Hybrid coordinator | `\|price - open\| > 2 × ATR` → RS = 0 |
| Live ATR | Exchange 1h OHLCV (29 candles) | Trend trailing stop | Replaces stale daily ATR |
| Intraday RSI | Exchange 1h OHLCV (29 candles) | Trend entry guard | `RSI(1h) >= 70` → skip entry |
| Volatility guard | `price_observations` 4h range | DCA Grid fill pause | `range > daily ATR` → pause fills |

---

## 7. Daily Data Pipeline

The pipeline runs at **02:00 UTC daily** via `bots/data_bot/main.py`, a simple asyncio script scheduled by cron.

```
data_bot.main(target_date)
│
├── Step 1: run_ohlcv_fetch(target_date)
│     ├── For each allow-listed asset:
│     │     GET /coins/{id}/ohlc?days=90          → OHLC data
│     │     GET /coins/{id}/market_chart?days=91  → volume + market cap
│     └── Upsert → inotives_tradings.asset_metrics_1d
│
├── Step 2: run_indicators_daily()
│     ├── Load last 400 days of OHLCV per asset
│     ├── Compute via pandas-ta:
│     │     ATR(14/20), ATR%, volatility_regime
│     │     SMA(20/50/200), EMA(12/26/50/200)
│     │     MACD(12,26,9), RSI(14), Bollinger Bands(20)
│     │     Volume SMA(20), volume ratio
│     │     ADX(14), EMA slope 5d%, volatility ratio ATR/StdDev
│     └── Upsert today → inotives_tradings.asset_indicators_1d
│
└── Step 3: run_regime_daily()
      ├── Load adx_14, ema_slope_5d, vol_ratio_14 from indicators
      ├── Normalise each to 0–100
      ├── Compute RS = score_adx×0.4 + score_slope×0.4 + score_vol×0.2
      └── Upsert today → inotives_tradings.asset_market_regime
```

### Running the pipeline

```bash
# Via Makefile
make daily-data                     # Yesterday (default)
make daily-data date=2026-03-14     # Specific date

# Direct invocation
uv run --env-file configs/envs/.env.local python -m bots.data_bot.main --date 2026-03-14
```

### Backfill commands

```bash
# Backfill indicators for specific assets
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.indicators import run_indicators_backfill; asyncio.run(run_indicators_backfill(asset_codes=['btc', 'eth']))"

# Backfill regime scores for all assets
uv run --env-file configs/envs/.env.local python -c \
    "import asyncio; from common.data.market_regime import run_regime_backfill; asyncio.run(run_regime_backfill())"
```

### Cron setup

```bash
# Install via cron manager
python -m common.tools.manage_cron install daily-data       # 02:00 UTC daily
python -m common.tools.manage_cron install coingecko-sync   # 01:00 UTC Sunday
```

---

## 8. Database Schema

All tables live in the `inotives_tradings` schema (trading data) or `coingecko` schema (raw reference data).

### Core Tables

#### `inotives_tradings.asset_indicators_1d`

Pre-computed daily technical indicators. One row per (asset, date).

| Column | Type | Description |
|---|---|---|
| `atr_14` | NUMERIC | ATR over 14 days — primary grid spacing input |
| `atr_pct` | NUMERIC | `atr_14 / close_price × 100` — ATR as % of price |
| `atr_sma_20` | NUMERIC | 20-day SMA of atr_14 — baseline for regime classification |
| `volatility_regime` | TEXT | `low` / `normal` / `high` / `extreme` (atr_14 vs atr_sma_20) |
| `sma_50` / `sma_200` | NUMERIC | Simple moving averages — entry filters |
| `ema_50` / `ema_200` | NUMERIC | Exponential MAs — golden cross, trend structure |
| `adx_14` | NUMERIC | Average Directional Index — trend strength |
| `ema_slope_5d` | NUMERIC | `((EMA50 - EMA50_5d_ago) / EMA50_5d_ago) × 100` |
| `vol_ratio_14` | NUMERIC | `ATR(14) / StdDev(Close, 14)` — noise filter |
| `rsi_14` | NUMERIC | RSI 14-day — overbought/oversold filter |
| `macd`, `macd_signal`, `macd_hist` | NUMERIC | MACD(12,26,9) |
| `bb_upper/middle/lower/width` | NUMERIC | Bollinger Bands(20, 2σ) |
| `volume_sma_20`, `volume_ratio` | NUMERIC | Volume vs 20-day average |

#### `inotives_tradings.asset_market_regime`

Daily regime scores. One row per (asset, date). Append-only.

| Column | Type | Description |
|---|---|---|
| `raw_adx` | NUMERIC | ADX(14) as-is |
| `raw_slope` | NUMERIC | EMA slope 5d% as-is |
| `raw_vol_ratio` | NUMERIC | Volatility ratio as-is |
| `score_adx` | NUMERIC | Normalised ADX → 0–100 |
| `score_slope` | NUMERIC | Normalised slope → 0–100 |
| `score_vol` | NUMERIC | Normalised vol ratio → 0–100 (inverted) |
| `final_regime_score` | NUMERIC | Weighted composite: 0–100 |

**Query example — last 7 days for BTC:**
```sql
SELECT r.metric_date,
       round(r.raw_adx,1)            AS adx,
       round(r.raw_slope,3)          AS slope_pct,
       round(r.score_adx,0)          AS s_adx,
       round(r.score_slope,0)        AS s_slope,
       round(r.score_vol,0)          AS s_vol,
       round(r.final_regime_score,1) AS rs
FROM inotives_tradings.asset_market_regime r
JOIN inotives_tradings.assets a ON a.id = r.asset_id
WHERE a.code = 'btc'
ORDER BY r.metric_date DESC
LIMIT 7;
```

#### `inotives_tradings.trade_strategies`

One row per configured strategy. Strategy-specific config in `metadata` JSONB.

| Column | Type | Description |
|---|---|---|
| `strategy_type` | TEXT | `DCA_GRID` or `TREND_FOLLOW` |
| `base_asset_id` | BIGINT | Asset being traded (e.g. BTC) |
| `quote_asset_id` | BIGINT | Quote currency (e.g. USDT) |
| `venue_id` | BIGINT | Trading venue |
| `maker_fee_pct` / `taker_fee_pct` | NUMERIC | Live-synced at bot startup |
| `status` | TEXT | `ACTIVE` / `PAUSED` / `ARCHIVED` |
| `metadata` | JSONB | All strategy-type-specific parameters |

#### `inotives_tradings.trade_cycles`

One row per grid cycle or trend trade.

| Column | Type | Description |
|---|---|---|
| `cycle_number` | INT | Incrementing counter per strategy |
| `capital_allocated` | NUMERIC | Quote currency deployed for this cycle |
| `status` | TEXT | `OPEN` / `CLOSING` / `CLOSED` |
| `close_trigger` | TEXT | `take_profit` / `stop_loss` / `trailing_stop` / `manual` |
| `stop_loss_price` | NUMERIC | Current effective stop (updated every tick for trend) |
| `metadata` | JSONB | DCA: grid params; Trend: entry/highest/stop state |

#### `inotives_tradings.trade_grid_levels`

Individual limit buy orders for the DCA Grid.

| Column | Type | Description |
|---|---|---|
| `level_num` | INT | Level 1 = shallowest, N = deepest |
| `target_price` | NUMERIC | Limit order price |
| `capital_allocated` | NUMERIC | Quote budgeted for this level |
| `quantity` | NUMERIC | Base asset to buy (fee-adjusted) |
| `status` | TEXT | `PENDING` / `OPEN` / `FILLED` / `CANCELLED` |
| `atr_value` | NUMERIC | ATR at time of level creation |
| `weight` | NUMERIC | Capital weight assigned to this level |

#### `inotives_tradings.capital_locks`

Capital reserved per active cycle, ensuring the bot can't over-allocate.

```sql
-- How much capital is free right now?
SELECT * FROM inotives_tradings.venue_available_capital
WHERE venue_id = 1;
```

---

## 9. Bot Runtime Architecture

### Process topology

```
Terminal 1: make pricing-bot
  └── bots.pricing_bot.main (asyncio polling, 60s)
        └── polls exchange tickers every 60s
        └── writes to inotives_tradings.price_observations

Terminal 2: make trader-bot
  └── bots.trader_bot.main (asyncio polling, 60s)
        ├── startup: sync live fees from exchange → inotives_tradings.trade_strategies
        └── each tick:
              load_active_strategies()  → N strategies (DCA + Trend per asset)
              for each strategy:
                dispatch(exchange, strategy)
                  └── DCA_GRID     → DcaGridStrategy.process()
                  └── TREND_FOLLOW → TrendFollowingStrategy.process()

Cron (02:00 UTC daily): make daily-data
  └── bots.data_bot.main
        ├── OHLCV fetch (CoinGecko)
        ├── Technical indicators (pandas-ta)
        └── Market regime scores
```

### One tick — DCA Grid (no open cycle, RS = 31.7)

```
1. Load latest indicators for the asset
2. get_regime_score_with_circuit_breaker()
   → RS = 31.7, no circuit breaker (price within 2×ATR of daily open)
3. RS < 61 → grid not paused
4. trend_has_priority()? RS=31.7 < 50 → NO, grid has priority
5. Scale capital: 1000 × (100-31.7)/100 = $683
6. Load current price from price_observations
7. _check_entry_conditions():
   - require_uptrend: price < SMA200? → YES (in downtrend)
   - defensive mode? RSI(1h) = 55 ≥ 40 → no bounce signal
8. → Log "no bounce signal" and return (no entry this tick)
```

### One tick — Trend Follow (no open cycle, RS = 31.7)

```
1. Load indicators (ema_50, ema_200, adx_14, etc.)
2. get_regime_score_with_circuit_breaker()
   → RS = 31.7
3. _check_entry_conditions():
   - min_regime_score: 31.7 < 61 → FAIL
4. → Log "regime_score=31.7 < 61.0" and return (idle)
```

### One tick — Trend Follow (RS = 74.3, no open cycle)

```
1. Indicators: ema_50=68000, ema_200=55000, adx=38, rsi=62, atr_pct=2.8%
2. RS = 74.3
3. grid_has_active_cycle()? → NO (grid paused at RS>61)
4. capital_allocated = 500 × 74.3/100 = $371.50
5. _check_entry_conditions():
   - RS 74.3 ≥ 61 ✓
   - EMA50 68000 > EMA200 55000 ✓
   - price 72000 > 5d_high 71500 ✓
   - ADX 38 ≥ 25 ✓
   - RSI 62 < 70 ✓
   - ATR% 2.8% < 6.0% ✓  → ALL PASS
6. Intraday RSI guard: RSI(1h) = 58 < 70 ✓
7. position_size = (371.50 × 0.01) / (2100 × 2.0) = 0.000885 BTC
8. Fee adjustment: 0.000885 / 1.005 = 0.000881 BTC
9. Place market BUY for 0.000881 BTC
10. Write trade_cycles + trade_orders + capital_locks + system_events
11. initial_stop = 72000 - (2×2100) = $67,800
```

---

## 10. Configuration Reference

### DCA Grid strategy metadata

```json
{
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
    "circuit_breaker_atr_pct":    8.0,
    "max_expansions":             1,
    "expansion_levels":           2,
    "expansion_capital_fraction": 0.3,
    "require_uptrend":            true,
    "require_golden_cross":       true,
    "force_entry":                false,
    "defensive_mode_enabled":     true,
    "defensive_atr_multiplier":   0.8,
    "defensive_profit_target":    2.5,
    "defensive_num_levels":       5,
    "defensive_rsi_oversold":     40,
    "defensive_rsi_timeframe":    "1h",
    "defensive_rsi_period":       14
}
```

### Trend Follow strategy metadata

```json
{
    "capital_allocated":    500,
    "risk_pct_per_trade":   1.0,
    "atr_stop_multiplier":  2.0,
    "atr_trail_multiplier": 3.0,
    "min_adx":              25.0,
    "min_regime_score":     61.0,
    "rsi_entry_max":        70.0,
    "max_atr_pct_entry":    6.0,
    "reserve_capital_pct":  20
}
```

### Hybrid Coordinator constants (`hybrid_coordinator.py`)

| Constant | Value | Meaning |
|---|---|---|
| `REGIME_GRID_PAUSE` | 61.0 | RS ≥ this → DCA Grid fully paused |
| `REGIME_PRIORITY_PIVOT` | 50.0 | RS > this → Trend has execution priority |
| `CIRCUIT_BREAKER_ATR_MULT` | 2.0 | Price deviation > N×ATR triggers override |

### Tuning the regime thresholds

The default weights `(ADX 0.4, Slope 0.4, Vol 0.2)` reflect a view that trend direction and strength are equally important, with volatility quality as a secondary filter.

To make the system **more trend-aggressive** (enters trend mode earlier):
- Lower `REGIME_GRID_PAUSE` from 61 → 55
- Lower `min_regime_score` in Trend Follow from 61 → 55

To make the system **more conservative** (prefers grid, only enters trend on very strong signals):
- Raise `REGIME_GRID_PAUSE` from 61 → 70
- Raise `min_regime_score` to 70
- Lower `atr_trail_multiplier` from 3.0 → 2.5 (tighter trailing stop — locks in profit faster)

---

*This document reflects the system as of INO-0003. See `CLAUDE.md` for current development state and next planned features.*
