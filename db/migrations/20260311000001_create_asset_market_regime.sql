-- migrate:up

-- Market regime scores computed from asset_indicators_1d.
-- Append-only time-series (one row per asset per day).
-- The Hybrid Grid strategy reads from this table to decide capital allocation:
--   RS 0–30   → 100% DCA Grid
--   RS 31–60  → sliding scale (Grid + Trend)
--   RS 61–100 → 100% Trend Following
--
-- Raw values are stored alongside normalized scores for full auditability —
-- if the bot takes a strange trade you can trace exactly which component
-- dragged the final score up or down.
CREATE TABLE base.asset_market_regime (
    id          BIGSERIAL PRIMARY KEY,
    asset_id    BIGINT NOT NULL REFERENCES base.assets(id) DEFERRABLE INITIALLY DEFERRED,
    metric_date DATE   NOT NULL,

    -- ── Raw indicator values (as-computed, before normalization) ──────────────
    raw_adx       NUMERIC(10, 6),   -- ADX(14) — trend strength 0–100
    raw_slope     NUMERIC(10, 6),   -- EMA50 5-day slope as %
    raw_vol_ratio NUMERIC(10, 6),   -- ATR(14) / StdDev(Close,14)

    -- ── Normalized component scores (each 0–100) ─────────────────────────────
    -- score_adx:
    --   ADX ≤ 15  → 0    (dead quiet)
    --   ADX = 25  → 50   (threshold of a trend)
    --   ADX ≥ 40  → 100  (max trend strength)
    --   Linear interpolation between breakpoints.
    score_adx     NUMERIC(10, 6),

    -- score_slope:
    --   slope ≤ 0%    → 0    (flat or downtrend)
    --   slope ≥ 0.5%  → 100  (strong uptrend)
    --   Linear scaling in between.
    score_slope   NUMERIC(10, 6),

    -- score_vol (inverted — low ratio = smooth trend = high score):
    --   ratio ≥ 1.2  → 0    (choppy, mean-reverting)
    --   ratio ≤ 0.8  → 100  (smooth, directional)
    --   Linear interpolation between breakpoints.
    score_vol     NUMERIC(10, 6),

    -- ── Final weighted regime score ───────────────────────────────────────────
    -- Formula: (score_adx × 0.4) + (score_slope × 0.4) + (score_vol × 0.2)
    final_regime_score NUMERIC(10, 6),

    -- ── Record metadata ───────────────────────────────────────────────────────
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,

    CONSTRAINT uq_asset_market_regime UNIQUE (asset_id, metric_date)
);

CREATE INDEX ON base.asset_market_regime (asset_id, metric_date DESC);
CREATE INDEX ON base.asset_market_regime (metric_date DESC);


-- migrate:down
DROP TABLE IF EXISTS base.asset_market_regime CASCADE;
