# Trading Bot Master Execution Plan: Hybrid Strategy & Regime-Switching

## 1. Core Architecture Overview
The system utilizes a **Regime-Switching Model** to dynamically allocate capital between **Mean Reversion (DCA Grid)** and **Momentum (Trend Following)**. This prevents the "death by a thousand cuts" in trending markets and captures gains during sideways chop.

---

## 2. Market Regime Controller (The Brain)
We use a **Regime Score (RS)** from 0 to 100 to determine the strategy mix.

### Calculation Logic
- **Frequency:** Primary calculation at **02:00 UTC Pipeline**.
- **Indicators:** ADX (Trend Strength), EMA Slope (Direction/Verticality), Volatility Ratio (ATR/StdDev).
- **Formula:** `RegimeScore = (ADX_Weight * 0.4) + (Slope_Weight * 0.4) + (Volatility_Weight * 0.2)`

### Strategy Allocation Mapping
| Score Range | Market State | Strategy Allocation |
| :--- | :--- | :--- |
| **0 - 30** | Deep Sideways | 100% DCA Grid / 0% Trend |
| **31 - 60** | Hybrid/Transition | Sliding Scale (e.g., 60% Grid, 40% Trend) |
| **61 - 100** | Strong Trend | 0% Grid / 100% Trend Following |

---

## 3. Strategy Logic

### A. Trend Following (Momentum)
- **Entry:** EMA 50 > EMA 200 AND Price > **5-Day High**.
- **Stop Loss:** Entry - (2 * ATR).
- **Trailing Stop:** Highest_Price_Since_Entry - (3 * ATR).
- **Re-entry:** If stopped out, wait for `Price > EMA50` AND `Price > New 5-Day High`.

### B. DCA Grid (Mean Reversion)
- **Operational Window:** Active when RS < 60.
- **Intensity:** Grid order size is multiplied by `(100 - RS) / 100`.

---

## 4. Conflict Resolution & Capital Management
To prevent "Double Exposure" (being 2x Long) and capital competition:

### A. The "Sliding Scale" Capital Lock
- **Trend_Limit:** `(RS / 100) * Total_Capital`
- **Grid_Limit:** `((100 - RS) / 100) * Total_Capital`

### B. Priority of Execution
- **If RS > 50:** Trend signals take priority. Grid orders may be cancelled/reduced to free up capital.
- **If RS < 50:** Grid orders take priority. Trend entries only trigger if excess liquidity exists.

### C. Transition Logic (Trend -> Grid)
- When RS drops, **do not hard-close** trend positions. 
- Stop opening *new* trend trades.
- Allow existing trend trades to exit via their **Trailing Stop** while the Grid begins placing orders.

---

## 5. Risk Management (ATR-Scaled Sizing)
Risk a fixed % of capital per trade, adjusted for volatility.
$$PositionSize = \\frac{AccountBalance \\times RiskPercentage}{ATR \\times StopMultiplier}$$

---

## 6. Circuit Breaker (Intraday)
While the regime is "anchored" at 02:00 UTC, the bot triggers an immediate re-calculation if:
1. `Current_Price` moves > 2 * ATR from the daily open.
2. `Hourly_Volatility` > 1.5 * `Daily_ATR`.

---

## 7. Database Schema
**Table:** `market_regime`
- `asset_pair`: VARCHAR
- `regime_score`: INT (0-100)
- `atr_value`: FLOAT
- `high_5_day`: FLOAT
- `timestamp`: DATETIME