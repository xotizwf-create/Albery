-- 061_telegram_agents.sql
-- Idempotent. Telegram-агенты, которых владелец заводит сам (как субагентов в Битриксе).
--
-- Why: до этого Telegram-агент был ровно один и жил одной строкой TG_AGENT_BOT_TOKEN в .env —
-- завести второго можно было только правкой файла на сервере и перезапуском службы.
--
-- Токен бота хранится здесь, потому что служба (albery-tg) и кабинет — разные процессы, а
-- общего секрет-хранилища у них нет. Наружу он не отдаётся НИКОГДА: API возвращает только
-- имя, @username и признак «токен задан».
CREATE TABLE IF NOT EXISTS telegram_agents (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    slug        text NOT NULL,                 -- ключ канала: он же bot в telegram_bot_messages
    name        text NOT NULL,                 -- имя как в Telegram (getMe.first_name)
    username    text,                          -- @username бота, без @
    bot_token   text NOT NULL,
    role_prompt text NOT NULL DEFAULT '',      -- кем агент работает: подставляется в промпт
    is_active   boolean NOT NULL DEFAULT true,
    bot_user_id bigint
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tg_agents_slug ON telegram_agents (slug);
