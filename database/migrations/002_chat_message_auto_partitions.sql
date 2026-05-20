-- =====================================================================
-- Migration 002: chat_messages auto partitions and partitions to 2030
-- =====================================================================

BEGIN;

CREATE OR REPLACE FUNCTION ensure_chat_messages_partition(target_day DATE) RETURNS void AS $$
DECLARE
    partition_start DATE;
    partition_end DATE;
    partition_name TEXT;
BEGIN
    IF target_day IS NULL THEN
        RAISE EXCEPTION 'message_day cannot be NULL';
    END IF;

    partition_start := date_trunc('month', target_day)::date;
    partition_end := (partition_start + INTERVAL '1 month')::date;
    partition_name := format('chat_messages_%s', to_char(partition_start, 'YYYY_MM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF chat_messages FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        partition_start,
        partition_end
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_chat_messages_ensure_partition() RETURNS TRIGGER AS $$
BEGIN
    PERFORM ensure_chat_messages_partition(NEW.message_day);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chat_messages_ensure_partition ON chat_messages;
CREATE TRIGGER trg_chat_messages_ensure_partition
    BEFORE INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION trg_chat_messages_ensure_partition();

DO $$
DECLARE
    month_start DATE := DATE '2027-01-01';
BEGIN
    WHILE month_start < DATE '2031-01-01' LOOP
        PERFORM ensure_chat_messages_partition(month_start);
        month_start := (month_start + INTERVAL '1 month')::date;
    END LOOP;
END$$;

COMMIT;

