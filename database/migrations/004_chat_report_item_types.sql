BEGIN;

ALTER TABLE chat_report_items
    DROP CONSTRAINT IF EXISTS chat_report_items_item_type_check;

ALTER TABLE chat_report_items
    ADD CONSTRAINT chat_report_items_item_type_check
    CHECK (item_type IN (
        'decision',
        'risk',
        'blocker',
        'commitment',
        'question',
        'unanswered_question',
        'goal',
        'action',
        'bitrix_reference',
        'result',
        'next_step',
        'note',
        'previous_day_task'
    ));

COMMIT;
