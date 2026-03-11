# Advanced DCA Grid Strategy (Volatility-Based)

## Overview

This document describes an advanced **volatility-harvesting DCA grid
strategy**.\
Instead of predicting market direction, the strategy profits from price
oscillations.

Key adaptive components:

-   ATR-based grid spacing
-   Weighted capital allocation
-   Dynamic profit targets
-   Volatility regime filters
-   Auto‑tuning grid parameters
-   Crash protection mechanisms

------------------------------------------------------------------------

# 1. Core Strategy Concept

Traditional grids use fixed spacing.

Example:

    Price = 100

    Buy orders
    99
    98
    97
    96
    95

This fails when volatility changes.

The improved system ties grid spacing to **ATR** so spacing expands or
contracts with volatility.

------------------------------------------------------------------------

# 2. Average True Range (ATR)

True Range:

    TR = max(
      High - Low,
      abs(High - PreviousClose),
      abs(Low - PreviousClose)
    )

ATR calculation:

    ATR(14) = Average(TR over last 14 periods)

------------------------------------------------------------------------

# 3. Convert ATR to Percentage

    ATR_percent = ATR / CurrentPrice

Example:

    Price = 100
    ATR = 3

    ATR_percent = 3%

------------------------------------------------------------------------

# 4. Volatility-Based Grid Spacing

    GridSpacing = ATR × Multiplier

Typical multiplier:

    0.4 – 0.6

Example:

    ATR = 3
    Multiplier = 0.5

    GridSpacing = 1.5%

------------------------------------------------------------------------

# 5. Weighted Capital Allocation

Instead of equal allocation, deeper grids receive more capital.

Example weights:

    1x
    1x
    2x
    3x
    3x

------------------------------------------------------------------------

# 6. Average Entry Price

    AvgEntry = (Σ(price × quantity)) / Σ(quantity)

------------------------------------------------------------------------

# 7. Profit Exit Rule

    SellPrice = AvgEntry × (1 + TargetProfit + Fees)

Example:

    AvgEntry = 96
    TargetProfit = 2%
    Fees = 0.1%

    SellPrice ≈ 98.0

------------------------------------------------------------------------

# 8. Volatility Regime Filter

Grid trading works best in sideways markets.

Example filter:

    ATR_percent < 6%
    AND
    Price within ±10% of 50-day moving average

If violated:

    Pause trading

------------------------------------------------------------------------

# 9. Inventory Risk Limit

    MaxInventory = 40% of total capital

Example:

    Capital = $10,000
    MaxInventory = $4,000

------------------------------------------------------------------------

# 10. Optimal Grid Level Calculation

    GridLevels = PriceRange / GridSpacing

Example:

    Expected swing = 20%
    Spacing = 2%

    GridLevels = 10

------------------------------------------------------------------------

# 11. Backtesting Methodology

Required historical data:

    OHLCV candles
    5m or 1h resolution
    ≥ 1 year history

Simulation:

    for candle in history:
        update ATR
        simulate grid fills
        update inventory
        record profit

Metrics:

    Total return
    Max drawdown
    Win rate
    Sharpe ratio

------------------------------------------------------------------------

# 12. Auto‑Tuning Grid Engine

The bot automatically adjusts parameters based on live volatility.

## Volatility Regime

  ATR %   Market
  ------- --------
  \<2%    Low
  2--5%   Normal
  \>5%    High

## Dynamic Grid Spacing

    Low volatility:
    GridSpacing = ATR × 0.4

    Normal volatility:
    GridSpacing = ATR × 0.5

    High volatility:
    GridSpacing = ATR × 0.7

## Dynamic Profit Target

    Low volatility: 1%
    Normal volatility: 1.5%
    High volatility: 2–3%

Pseudo algorithm:

    while trading:

        fetch price
        compute ATR
        detect volatility regime

        adjust grid spacing
        adjust grid levels
        adjust profit target

------------------------------------------------------------------------

# 13. Crash Protection Layer

Grid systems fail mainly during **large market crashes**.\
A crash protection module prevents runaway inventory accumulation.

------------------------------------------------------------------------

## 13.1 Volatility Circuit Breaker

If volatility spikes abnormally, pause the system.

Example:

    if ATR_percent > 8%:
        pause trading

This prevents entering during panic markets.

------------------------------------------------------------------------

## 13.2 Trend Break Detection

Detect strong downtrends using moving averages.

Example:

    if Price < 200MA
    AND 50MA < 200MA:
        disable buy grids

Only allow selling until trend stabilizes.

------------------------------------------------------------------------

## 13.3 Capital Reserve

Never deploy all capital into grids.

Example:

    ReserveCapital = 30%
    GridCapital = 70%

Reserve capital allows buying during extreme crashes.

------------------------------------------------------------------------

## 13.4 Emergency Stop Loss

If drawdown exceeds a threshold:

    if PortfolioDrawdown > 20%:
        close all positions
        stop trading

This protects against catastrophic losses.

------------------------------------------------------------------------

## 13.5 Dynamic Grid Expansion

During crashes:

    Add deeper grids
    Increase spacing
    Reduce order size

Example:

    100
    97
    94
    91
    88

------------------------------------------------------------------------

# 14. Strategy Advantages

    No directional prediction required
    Harvests volatility
    Auto‑adjusts to market conditions
    Highly automatable

------------------------------------------------------------------------

# 15. Strategy Risks

    Strong sustained trends
    Exchange outages
    Low liquidity assets
    Extreme flash crashes

Proper crash protection reduces these risks.

------------------------------------------------------------------------

# Key Principle

The system profits by **harvesting volatility rather than predicting
price direction**.
