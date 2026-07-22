-- 060_telegram_agent_journal.sql
-- Idempotent. Telegram side of the agent centre: the message journal and per-bot access.
--
-- Why: Bitrix conversations have lived in bitrix_bot_messages since 052 and the UI is built on
-- them, while Telegram was logged only to a jsonl file next to the service and to the agent's
-- state file. So the owner could not see in the cabinet what the agent said to leads — the
-- Telegram tab was a placeholder.
--
-- Two channels share ONE bot token (@Albery_AI2_Bot): direct messages to the bot itself, and the
-- business-mode conversations of the company account @AlberyAIManager. They are separate agents
-- for the owner, so `bot` keys the channel and `kind` splits the manager's own DMs from the
-- conversations with leads.
--
-- Scope (owner's decision 2026-07-22): only chats the AGENT took part in are journalled. Business
-- mode also sees the account's private chats with suppliers and friends; those must not land in
-- the company cabinet.
CREATE TABLE IF NOT EXISTS telegram_bot_messages (
    id              bigserial PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    bot             text NOT NULL,                 -- канал: 'albery-ai-bot' | 'albery-ai-manager'
    dialog_id       text NOT NULL,                 -- telegram id собеседника (или чата)
    tg_user_id      bigint,
    username        text,                          -- без @, нижний регистр
    display_name    text,
    direction       text NOT NULL,                 -- 'in' | 'out'
    kind            text NOT NULL DEFAULT 'bot_dm',-- bot_dm | lead_chat | system
    text            text NOT NULL DEFAULT '',
    tg_message_id   bigint,
    status          text NOT NULL DEFAULT 'ok',    -- ok | error (сбой хода агента)
    meta            jsonb
);
CREATE INDEX IF NOT EXISTS idx_tbm_dialog  ON telegram_bot_messages (bot, dialog_id, id);
CREATE INDEX IF NOT EXISTS idx_tbm_created ON telegram_bot_messages (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tbm_kind    ON telegram_bot_messages (bot, kind, id);

-- Кто может писать агенту в Telegram. До этого белый список жил одной строкой в .env
-- (TG_AGENT_OWNER_USERNAMES), менялся только руками на сервере и был общим на все каналы.
-- Telegram не позволяет узнать id по @username заранее, поэтому основной ключ — username;
-- tg_user_id заполняется, как только человек написал.
CREATE TABLE IF NOT EXISTS telegram_bot_access (
    id           bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    bot          text NOT NULL,
    username     text NOT NULL,                    -- без @, нижний регистр
    tg_user_id   bigint,
    display_name text,
    is_active    boolean NOT NULL DEFAULT true,
    note         text
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tba_bot_username ON telegram_bot_access (bot, username);
