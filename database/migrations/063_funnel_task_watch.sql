-- 063_funnel_task_watch.sql
-- Idempotent. Ожидания агента воронки: задача сотруднику → сообщение клиенту при её закрытии.
--
-- Why: 23.07.2026 агент поставил задачу «Подписать договор в ЭДО», владелец её выполнил и
-- написал «документы направлены» — а клиент об этом не узнал. Агент отвечает только на входящие
-- сообщения, поэтому событие «задача закрылась» до него не доходило вовсе. Клиент сидел и ждал.
--
-- Здесь ровно то, чего не хватает: связка «задача ↔ сделка ↔ клиент» и отметка, что клиенту уже
-- сказали. Без отметки сторож при каждом проходе слал бы одно и то же сообщение.
CREATE TABLE IF NOT EXISTS funnel_task_watch (
    id              bigserial PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    bitrix_task_id  bigint NOT NULL,
    deal_id         bigint,
    telegram_id     bigint NOT NULL,        -- кому писать, когда задача закроется
    kind            text NOT NULL,          -- edo | invoice | paper | other
    client_message  text NOT NULL DEFAULT '',  -- что именно сказать клиенту
    next_stage      text,                   -- стадия сделки после закрытия задачи
    notified_at     timestamptz,            -- клиенту уже сообщено
    cancelled_at    timestamptz,            -- ожидание снято (задача удалена/неактуальна)
    note            text
);
-- Один активный сторож на задачу: повторная постановка не должна задваивать сообщения клиенту.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ftw_task ON funnel_task_watch (bitrix_task_id)
    WHERE notified_at IS NULL AND cancelled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ftw_pending ON funnel_task_watch (notified_at, cancelled_at);
