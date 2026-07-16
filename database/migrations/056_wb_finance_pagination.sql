-- 056_wb_finance_pagination.sql
-- Durable pagination for the WB detailed financial report.  A Base token can
-- call this method only twice per day, so every successful 100k page must be
-- committed and resumed by rrdId without restarting the six-month range.
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS range_from date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS range_to date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS page_cursor bigint NOT NULL DEFAULT 0;

-- Monetary fields used by the current detailed-report contract.  The complete
-- original object remains in raw; these columns make dashboard totals auditable.
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS paid_acceptance numeric(18,2);
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS additional_payment numeric(18,2);
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS installment_cofinancing_amount numeric(18,2);
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS cashback_amount numeric(18,2);
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS cashback_discount numeric(18,2);
ALTER TABLE wb_finance_details ADD COLUMN IF NOT EXISTS cashback_commission_change numeric(18,2);
