-- Владелец 30.07.2026: этапы воронки меняют только человек или явный MCP-инструмент ИИ.
-- Старые post-delivery действия могли остаться pending/retry/leased после уже доставленного
-- сообщения. Завершаем их без записи в сделку; действия UI/MCP имеют outbox_id IS NULL.
UPDATE funnel_workspace_crm_actions
   SET processing_status = 'done',
       result = jsonb_build_object(
           'status', 'skipped',
           'reason', 'automatic_stage_transitions_disabled'
       ),
       locked_at = NULL,
       locked_until = NULL,
       locked_by = NULL,
       last_error = NULL,
       completed_at = COALESCE(completed_at, now()),
       updated_at = now()
 WHERE action_type = 'move_stage'
   AND outbox_id IS NOT NULL
   AND processing_status IN ('pending', 'leased', 'retry');
