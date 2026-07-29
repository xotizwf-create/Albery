-- 076_iu_form_merges.sql
-- Idempotent. Журнал склеек «анкета → карточка человека».
--
-- Why: вотчер видит одну и ту же формовую сделку столько раз, сколько раз он запустится до
-- её обработки, а поля анкеты нельзя переносить дважды. Ключ — id самой формовой сделки:
-- обработали один раз, и запись об этом переживает рестарт.
--
-- `payload` хранит СНИМОК удалённой карточки целиком. Удаление сделки в Битриксе необратимо,
-- поэтому перед ним всё её содержимое ложится сюда: если склейка окажется ошибочной, данные
-- заявки не потеряны и восстанавливаются руками.
CREATE TABLE IF NOT EXISTS iu_form_merges (
    form_deal_id   bigint PRIMARY KEY,
    target_deal_id bigint,
    telegram_id    bigint,
    matched_by     text NOT NULL,          -- token | username | none
    merged_at      timestamptz NOT NULL DEFAULT now(),
    deleted_form   boolean NOT NULL DEFAULT false,
    note           text,
    payload        jsonb
);

CREATE INDEX IF NOT EXISTS idx_iu_form_merges_target
    ON iu_form_merges (target_deal_id)
    WHERE target_deal_id IS NOT NULL;
