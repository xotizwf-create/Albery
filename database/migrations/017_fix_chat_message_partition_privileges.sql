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

    IF to_regclass(format('public.%I', partition_name)) IS NOT NULL THEN
        RETURN;
    END IF;

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF chat_messages FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        partition_start,
        partition_end
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

GRANT EXECUTE ON FUNCTION ensure_chat_messages_partition(DATE) TO PUBLIC;

COMMIT;
