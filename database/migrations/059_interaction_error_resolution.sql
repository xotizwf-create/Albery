-- 059_interaction_error_resolution.sql
-- Idempotent. Снятие метки «ОШИБКА» с диалога (владелец 2026-07-20).
--
-- Метка в интерфейсе считала ВСЕ ходы со статусом <> 'ok' за всю историю диалога, поэтому
-- один давний таймаут навсегда помечал переписку как проблемную и убрать это было нечем.
-- Теперь разобранную ошибку можно закрыть, указав номер задачи Битрикса, в которой она
-- устранена: счётчик учитывает только НЕразобранные, а история разбора остаётся видимой.

ALTER TABLE bitrix_bot_interactions ADD COLUMN IF NOT EXISTS error_resolved_at   timestamptz;
ALTER TABLE bitrix_bot_interactions ADD COLUMN IF NOT EXISTS error_resolved_by   text;
ALTER TABLE bitrix_bot_interactions ADD COLUMN IF NOT EXISTS error_resolved_task bigint;
ALTER TABLE bitrix_bot_interactions ADD COLUMN IF NOT EXISTS error_resolved_note text;

-- Счётчик метки бьёт по (agent_slug, dialog_id) среди неразобранных ошибок.
CREATE INDEX IF NOT EXISTS bitrix_bot_interactions_unresolved_errors_idx
    ON bitrix_bot_interactions (agent_slug, dialog_id)
    WHERE status <> 'ok' AND error_resolved_at IS NULL;
