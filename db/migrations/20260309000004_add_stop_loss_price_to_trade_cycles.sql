-- migrate:up

-- Add stop_loss_price to trade_cycles.
-- Stores the absolute price level at which the cycle should be force-closed.
-- Set at cycle open by the bot based on strategy parameters (e.g. lowest grid
-- level, or avg_entry - N*ATR). Queried on every price tick to decide whether
-- to trigger an early exit.
ALTER TABLE base.trade_cycles
    ADD COLUMN stop_loss_price NUMERIC(36, 18);

-- Index supports the bot's per-tick check:
--   SELECT id FROM base.trade_cycles WHERE status = 'OPEN' AND stop_loss_price IS NOT NULL
CREATE INDEX ON base.trade_cycles (status, stop_loss_price) WHERE stop_loss_price IS NOT NULL;


-- migrate:down
DROP INDEX IF EXISTS base.trade_cycles_status_stop_loss_price_idx;
ALTER TABLE base.trade_cycles DROP COLUMN IF EXISTS stop_loss_price;
