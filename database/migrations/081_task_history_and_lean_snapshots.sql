-- История изменений задач Битрикса + похудение снимков.
--
-- Зачем. На вопрос «кто просрачивал задачи» система ответить не могла: снимок состояния
-- (bitrix_task_snapshots) писался по расписанию синхронизации — 18 раз в сутки все 937 задач,
-- ~17 000 строк в день, из них ~99% дубли предыдущего состояния. При этом автора изменения
-- в снимке нет вовсе, а поле raw_json (полная копия карточки, 6 КБ на строку) занимало
-- 2077 МБ из 2453 МБ таблицы и дублировало bitrix_tasks.raw_json. Ни один из 148 инструментов
-- агента снимки не читал — данные копились два месяца вхолостую и росли на 100 МБ в сутки.
--
-- Ключевое наблюдение: синхронизация УЖЕ вызывает tasks.task.history.list на каждую задачу
-- (bitrix.py::get_task_history) и складывает ответ в raw_json, откуда его никто не достаёт.
-- А это родной аудит Битрикса: кто, какое поле, из чего в что, когда. На 06.08.2026 там
-- 3467 записей по 937 задачам, включая 147 переносов дедлайна — то есть история восстанавливается
-- ЗАДНИМ ЧИСЛОМ, без единого дополнительного обращения к порталу.
--
-- Поэтому: заводим нормальную таблицу истории, а снимок оставляем как дешёвый «срез на дату»
-- (без raw_json, только при реальном изменении).

CREATE TABLE IF NOT EXISTS bitrix_task_history (
    id                      bigserial PRIMARY KEY,
    -- id записи истории на портале: единственный надёжный ключ дедупликации. История
    -- перечитывается при каждой синхронизации, и без него мы плодили бы дубли.
    bitrix_history_id       bigint NOT NULL,
    bitrix_task_id          bigint NOT NULL,
    task_id                 uuid REFERENCES bitrix_tasks(id) ON DELETE CASCADE,
    field                   text NOT NULL,
    value_from              text,
    value_to                text,
    changed_by_bitrix_user_id bigint,
    changed_by_name         text,
    changed_at              timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bitrix_history_id)
);

-- Отчёт «кто просрачивал» ходит по полю + периоду (DEADLINE / RESPONSIBLE_ID / STATUS).
CREATE INDEX IF NOT EXISTS idx_bth_field_changed
    ON bitrix_task_history (field, changed_at DESC);
-- История конкретной задачи — самый частый одиночный запрос.
CREATE INDEX IF NOT EXISTS idx_bth_task
    ON bitrix_task_history (bitrix_task_id, changed_at DESC);
-- «Что менял этот сотрудник за период».
CREATE INDEX IF NOT EXISTS idx_bth_user_changed
    ON bitrix_task_history (changed_by_bitrix_user_id, changed_at DESC)
    WHERE changed_by_bitrix_user_id IS NOT NULL;

-- Снимки: убираем дублирующий блоб. Освобождает 2077 МБ из 2453 МБ; поля, ради которых
-- таблица заведена (статус, приоритет, ответственный, дедлайн, дата закрытия), остаются
-- нетронутыми — история состояний с 25.06.2026 сохраняется полностью.
ALTER TABLE bitrix_task_snapshots DROP COLUMN IF EXISTS raw_json;

-- Снимок теперь пишется только при изменении, поэтому нужен быстрый доступ к последнему
-- состоянию задачи — с ним сравнивается свежее.
CREATE INDEX IF NOT EXISTS idx_bts_task_latest
    ON bitrix_task_snapshots (bitrix_task_id, created_at DESC);
