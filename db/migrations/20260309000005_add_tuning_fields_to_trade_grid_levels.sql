-- migrate:up

-- Add auto-tuning and crash-expansion context to each grid level.
--
-- atr_value and atr_multiplier capture the exact inputs that produced
-- target_price at creation time — essential for backtesting replay and
-- auditing why a level was placed at a specific price.
--
-- level_trigger distinguishes levels created at cycle open from those
-- added dynamically by the crash protection module (Section 13.5).
ALTER TABLE base.trade_grid_levels
    ADD COLUMN atr_value     NUMERIC(36, 18),  -- ATR snapshot when this level was created
    ADD COLUMN atr_multiplier NUMERIC(10, 6),  -- Regime multiplier applied: 0.4 | 0.5 | 0.7
    ADD COLUMN level_trigger  TEXT NOT NULL DEFAULT 'initial';  -- 'initial' | 'crash_expansion' | 'rebalance'

ALTER TABLE base.trade_grid_levels
    ADD CONSTRAINT chk_trade_grid_level_trigger
        CHECK (level_trigger IN ('initial', 'crash_expansion', 'rebalance'));


-- migrate:down
ALTER TABLE base.trade_grid_levels DROP CONSTRAINT IF EXISTS chk_trade_grid_level_trigger;
ALTER TABLE base.trade_grid_levels
    DROP COLUMN IF EXISTS level_trigger,
    DROP COLUMN IF EXISTS atr_multiplier,
    DROP COLUMN IF EXISTS atr_value;
