-- migrate:up

-- Pre-computed daily technical indicators derived from base.asset_metrics_1d.
-- Populated nightly by the indicators pipeline after new OHLCV data arrives.
-- Strategy bots read from here instead of recomputing on every poll cycle.
--
-- All indicator values are nullable — a NULL means insufficient history was
-- available to compute that indicator for the given date (e.g. SMA(200) needs
-- at least 200 prior closes).
CREATE TABLE base.asset_indicators_1d (
    id          BIGSERIAL PRIMARY KEY,
    asset_id    BIGINT NOT NULL REFERENCES base.assets(id) DEFERRABLE INITIALLY DEFERRED,
    metric_date DATE   NOT NULL,

    -- ── Volatility ────────────────────────────────────────────────────────────
    -- Average True Range: measures daily price range normalised against gaps
    atr_14      NUMERIC(36, 18),   -- ATR over 14 days  (primary grid-spacing input)
    atr_20      NUMERIC(36, 18),   -- ATR over 20 days  (smoother, longer regime view)

    -- ── Trend — Moving Averages ───────────────────────────────────────────────
    sma_20      NUMERIC(36, 18),   -- 20-day simple MA   (short-term trend)
    sma_50      NUMERIC(36, 18),   -- 50-day simple MA   (medium-term trend)
    sma_200     NUMERIC(36, 18),   -- 200-day simple MA  (long-term trend / entry filter)
    ema_12      NUMERIC(36, 18),   -- 12-day exponential MA (MACD fast line)
    ema_26      NUMERIC(36, 18),   -- 26-day exponential MA (MACD slow line)

    -- ── Trend — MACD ─────────────────────────────────────────────────────────
    macd        NUMERIC(36, 18),   -- ema_12 - ema_26
    macd_signal NUMERIC(36, 18),   -- 9-day EMA of macd
    macd_hist   NUMERIC(36, 18),   -- macd - macd_signal  (histogram / momentum strength)

    -- ── Momentum ─────────────────────────────────────────────────────────────
    rsi_14      NUMERIC(10, 6),    -- Relative Strength Index 14-day (0–100)

    -- ── Volatility Bands ─────────────────────────────────────────────────────
    -- Bollinger Bands: SMA(20) ± 2 * stddev(20)
    bb_upper    NUMERIC(36, 18),   -- Upper band  (overbought / resistance)
    bb_middle   NUMERIC(36, 18),   -- Middle band (= sma_20)
    bb_lower    NUMERIC(36, 18),   -- Lower band  (oversold / support)
    bb_width    NUMERIC(10, 6),    -- (upper - lower) / middle * 100  (volatility proxy)

    -- ── Volume ───────────────────────────────────────────────────────────────
    volume_sma_20  NUMERIC(36, 2), -- 20-day average daily volume (USD)
    volume_ratio   NUMERIC(10, 6), -- today volume / volume_sma_20  (>1 = above-avg activity)

    -- ── Derived grid inputs (pre-computed for the adaptive strategy) ──────────
    -- These avoid recomputation in the bot and make backtesting reproducible.
    atr_pct         NUMERIC(10, 6),  -- atr_14 / close_price * 100  (ATR as % of price)
    atr_sma_20      NUMERIC(36, 18), -- 20-day SMA of atr_14  (baseline volatility regime)
    volatility_regime TEXT,          -- 'low' | 'normal' | 'high' | 'extreme'
                                     -- derived from atr_14 vs atr_sma_20 bands

    -- ── Record metadata ───────────────────────────────────────────────────────
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_asset_indicators_1d UNIQUE (asset_id, metric_date)
);

CREATE INDEX ON base.asset_indicators_1d (asset_id, metric_date DESC);
CREATE INDEX ON base.asset_indicators_1d (metric_date DESC);

-- Auditing trigger only (no soft delete / versioning for computed time-series)
CREATE TRIGGER auditing_trigger_asset_indicators_1d
    BEFORE INSERT OR UPDATE ON base.asset_indicators_1d
    FOR EACH ROW EXECUTE PROCEDURE base.set_audit_fields();


-- migrate:down
DROP TABLE IF EXISTS base.asset_indicators_1d CASCADE;
