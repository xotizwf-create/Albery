-- Смена этапа воронки оператором из рабочего окна.
--
-- Исходное ограничение таблицы действий писалось под единственный сценарий: этап
-- двигает доставка сообщения, поэтому строка `move_stage` обязана ссылаться на
-- отправленное сообщение (`outbox_id IS NOT NULL`). Оператор меняет этап руками, без
-- какой-либо отправки, и такая строка отвергалась проверкой.
--
-- Требование к самому этапу сохраняется: он обязан быть непустым и не длиннее 200
-- символов — иначе в CRM уехал бы мусор.

DO $$
DECLARE
    old_check record;
BEGIN
    FOR old_check IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'funnel_workspace_crm_actions'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%move_stage%'
           AND pg_get_constraintdef(oid) LIKE '%outbox_id IS NOT NULL%'
           AND pg_get_constraintdef(oid) LIKE '%delivery_effects%'
    LOOP
        EXECUTE format(
            'ALTER TABLE funnel_workspace_crm_actions DROP CONSTRAINT %I',
            old_check.conname
        );
        EXECUTE format(
            'ALTER TABLE funnel_workspace_crm_actions ADD CONSTRAINT %I CHECK ('
            || '(action_type = ''ensure_deal'' AND outbox_id IS NULL AND target_stage IS NULL)'
            || ' OR (action_type = ''delivery_effects'' AND outbox_id IS NOT NULL AND target_stage IS NULL)'
            || ' OR (action_type = ''move_stage'' AND target_stage IS NOT NULL'
            || ' AND char_length(btrim(target_stage)) BETWEEN 1 AND 200)'
            || ') NOT VALID',
            old_check.conname
        );
        EXECUTE format(
            'ALTER TABLE funnel_workspace_crm_actions VALIDATE CONSTRAINT %I',
            old_check.conname
        );
    END LOOP;
END
$$;
