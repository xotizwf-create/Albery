-- 033: analysis views linking employee error reports to the dialogue that preceded them.
-- Idempotent (CREATE OR REPLACE VIEW); listed in ensure_postgres ALWAYS_APPLY_MIGRATIONS so a
-- changed definition is re-applied on every deploy.

-- error_report_context: one row per complaint with the last up-to-8 dialogue turns before it
-- (as a JSON array `context_turns`) — "what the person was discussing when they complained".
CREATE OR REPLACE VIEW error_report_context AS
SELECT
    r.id            AS report_id,
    r.created_at    AS reported_at,
    r.dialog_id,
    r.bitrix_user_id,
    r.reporter_name,
    r.report_text,
    r.delivered,
    r.delivery_error,
    ctx.context_turns
FROM bitrix_error_reports r
LEFT JOIN LATERAL (
    SELECT json_agg(t ORDER BY t.created_at) AS context_turns
    FROM (
        SELECT i.created_at, i.question, i.answer, i.status, i.error
        FROM bitrix_bot_interactions i
        WHERE i.dialog_id = r.dialog_id
          AND i.created_at <= r.created_at
        ORDER BY i.created_at DESC
        LIMIT 8
    ) t
) ctx ON TRUE;

-- dialog_timeline: a single chronological stream per dialog that interleaves Q/A turns and the
-- complaints themselves (kind='turn' | 'complaint') — so complaints sit inline in the dialogue.
CREATE OR REPLACE VIEW dialog_timeline AS
SELECT dialog_id, bitrix_user_id, created_at,
       'turn'::text AS kind, id AS source_id,
       question AS user_text, answer AS bot_text, status, error
FROM bitrix_bot_interactions
UNION ALL
SELECT dialog_id, bitrix_user_id, created_at,
       'complaint'::text AS kind, id AS source_id,
       report_text AS user_text, NULL::text AS bot_text,
       CASE WHEN delivered THEN 'delivered' ELSE 'delivery_failed' END AS status,
       delivery_error AS error
FROM bitrix_error_reports;
