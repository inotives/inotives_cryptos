-- migrate:up

-- Add regime-detection indicators needed for the Hybrid Grid + Trend Following strategy.
--
-- ema_50, ema_200   : Medium and long-term EMAs for golden/death cross detection
--                     and trend-following entry filter (price > EMA50 > EMA200).
-- adx_14            : Average Directional Index (14-period). Measures trend *strength*
--                     independent of direction. ≥25 = trending, ≥40 = strong trend.
-- ema_slope_5d      : ((EMA50_today - EMA50_5d_ago) / EMA50_5d_ago) * 100
--                     Rate of change of EMA50 over 5 days, expressed as %.
--                     Captures trend *direction and velocity*. Normalised: 0%→0pts, ≥0.5%→100pts.
-- vol_ratio_14      : ATR(14) / StdDev(Close, 14)
--                     Efficiency ratio. Low ratio (<0.8) = smooth, directional move.
--                     High ratio (>1.2) = choppy, mean-reverting noise.

ALTER TABLE inotives_tradings.asset_indicators_1d
    ADD COLUMN IF NOT EXISTS ema_50       NUMERIC(36, 18),
    ADD COLUMN IF NOT EXISTS ema_200      NUMERIC(36, 18),
    ADD COLUMN IF NOT EXISTS adx_14       NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS ema_slope_5d NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS vol_ratio_14 NUMERIC(10, 6);

COMMENT ON COLUMN inotives_tradings.asset_indicators_1d.ema_50
    IS '50-day exponential MA — medium-term trend reference';
COMMENT ON COLUMN inotives_tradings.asset_indicators_1d.ema_200
    IS '200-day exponential MA — long-term trend reference';
COMMENT ON COLUMN inotives_tradings.asset_indicators_1d.adx_14
    IS 'Average Directional Index (14-day). Trend strength 0–100. ≥25 trending, ≥40 strong trend.';
COMMENT ON COLUMN inotives_tradings.asset_indicators_1d.ema_slope_5d
    IS '((EMA50_today - EMA50_5d_ago) / EMA50_5d_ago) * 100. Rate of change of EMA50 as %.';
COMMENT ON COLUMN inotives_tradings.asset_indicators_1d.vol_ratio_14
    IS 'ATR(14) / StdDev(Close,14). <0.8 = smooth trend (high score), >1.2 = choppy noise (low score).';


-- migrate:down

ALTER TABLE inotives_tradings.asset_indicators_1d
    DROP COLUMN IF EXISTS ema_50,
    DROP COLUMN IF EXISTS ema_200,
    DROP COLUMN IF EXISTS adx_14,
    DROP COLUMN IF EXISTS ema_slope_5d,
    DROP COLUMN IF EXISTS vol_ratio_14;
