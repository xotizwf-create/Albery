-- 064_agent_decisions.sql
-- Idempotent. Трасса решений агента воронки: что решили, по какому правилу и на каких фактах.
--
-- Why: 24-25.07.2026 три сбоя подряд разбирались раскопками — по журналу сообщений было видно
-- ЧТО ушло клиенту, но не видно ПОЧЕМУ. «Агент тупит» приходилось превращать в факты вручную:
-- читать код, восстанавливать состояние, гадать, какое условие сработало. Владелец платил за это
-- своим временем, а каждый разбор занимал часы.
--
-- Здесь пишется само решение: слой (ход по сообщению или сторож), сработавшее правило с его
-- причиной, действие и снимок фактов. По одной строке видно, почему агент поступил так, а не
-- иначе, — и видно СРАЗУ, без чтения кода.
CREATE TABLE IF NOT EXISTS agent_decisions (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    dialog_id   bigint NOT NULL,            -- telegram id собеседника
    deal_id     bigint,                     -- сделка воронки, если она уже есть
    slot        text NOT NULL,              -- message | watch
    rule        text NOT NULL,              -- сработавшее правило реестра
    action      text NOT NULL,              -- что решили сделать
    origin      text NOT NULL DEFAULT '',   -- причина появления правила (дата + живой случай)
    facts       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- снимок состояния на момент решения
    outcome     text NOT NULL DEFAULT ''    -- что вышло на самом деле (отправлено/эскалировано)
);
-- Разбор всегда идёт по одному диалогу и от свежего к старому.
CREATE INDEX IF NOT EXISTS idx_agent_decisions_dialog ON agent_decisions (dialog_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_at ON agent_decisions (created_at DESC);
