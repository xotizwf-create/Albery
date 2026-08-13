-- Bitrix message delivery has no caller supplied idempotency key. Persist the
-- provider-call boundary so a timeout/crash cannot cause a blind duplicate.
ALTER TABLE iu_manager_wait_alerts
    ADD COLUMN IF NOT EXISTS provider_message_id text;

DO $$
DECLARE
    status_constraint_name text;
    status_constraint_definition text;
BEGIN
    SELECT conname, pg_get_constraintdef(oid) AS definition
      INTO status_constraint_name, status_constraint_definition
      FROM pg_constraint
     WHERE conrelid = 'iu_manager_wait_alerts'::regclass
       AND contype = 'c'
       AND lower(pg_get_constraintdef(oid)) LIKE '%status%'
       AND lower(pg_get_constraintdef(oid)) LIKE '%pending%'
       AND lower(pg_get_constraintdef(oid)) LIKE '%leased%'
       AND lower(pg_get_constraintdef(oid)) LIKE '%sent%'
       AND lower(pg_get_constraintdef(oid)) LIKE '%cancelled%'
     LIMIT 1;

    IF status_constraint_name IS NULL THEN
        ALTER TABLE iu_manager_wait_alerts
            ADD CONSTRAINT iu_manager_wait_alerts_status_check
            CHECK (status IN (
                'pending', 'leased', 'sending', 'sent', 'unknown', 'cancelled'
            )) NOT VALID;
        ALTER TABLE iu_manager_wait_alerts
            VALIDATE CONSTRAINT iu_manager_wait_alerts_status_check;
    ELSIF lower(status_constraint_definition) NOT LIKE '%sending%'
       OR lower(status_constraint_definition) NOT LIKE '%unknown%' THEN
        EXECUTE format(
            'ALTER TABLE iu_manager_wait_alerts DROP CONSTRAINT %I',
            status_constraint_name
        );
        EXECUTE format(
            'ALTER TABLE iu_manager_wait_alerts ADD CONSTRAINT %I '
            || 'CHECK (status IN (''pending'', ''leased'', ''sending'', ''sent'', '
            || '''unknown'', ''cancelled'')) NOT VALID',
            status_constraint_name
        );
        EXECUTE format(
            'ALTER TABLE iu_manager_wait_alerts VALIDATE CONSTRAINT %I',
            status_constraint_name
        );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_iu_manager_wait_alerts_ambiguous
    ON iu_manager_wait_alerts (updated_at, id)
    WHERE status = 'unknown';
