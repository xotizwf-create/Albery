-- 090_avito_channel.sql
-- Канал Авито: аккаунты и регистрация источника в общем журнале переписок.
--
-- Переписки, сообщения и очередь отправки НЕ дублируются: канал живёт в тех же таблицах
-- funnel_workspace_* (миграция 070), которые с самого начала сделаны транспортно-нейтральными.
-- Аккаунт Авито пишется в существующее поле диалога business_connection_id — там же, где у
-- Telegram лежит его business-соединение, то есть «через какое НАШЕ подключение идёт разговор».
-- Поэтому мультиаккаунт не требует ни новой таблицы диалогов, ни изменения ключа уникальности.
--
-- Отдельная таблица нужна только для самих аккаунтов: у веб-сессии Авито есть состояние
-- (жива / нужен повторный вход / заблокирована), которого у Telegram-бота нет.

INSERT INTO funnel_workspace_sources (source_key, source_type, display_name)
VALUES ('avito', 'avito_web', 'Авито')
ON CONFLICT (source_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS avito_accounts (
    slug             text PRIMARY KEY
                     CHECK (slug ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    label            text NOT NULL,
    -- Профиль браузера и куки НИКОГДА не лежат в базе: здесь только путь к каталогу в
    -- защищённой зоне, чтобы воркер знал, какую сессию открывать.
    profile_dir      text,
    -- Через какой выход ходит этот аккаунт: понятная человеку метка, не адрес и не пароль
    -- («компьютер владельца», «мобильный прокси»). Реальные реквизиты — в окружении воркера.
    egress_label     text,
    session_status   text NOT NULL DEFAULT 'unknown'
                     CHECK (session_status IN ('unknown', 'ok', 'needs_login', 'blocked', 'error')),
    session_checked_at timestamptz,
    last_error       text,
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_avito_accounts_active
    ON avito_accounts (is_active, slug);

-- Диалоги Авито ищутся по аккаунту (левая колонка интерфейса — вкладки аккаунтов).
CREATE INDEX IF NOT EXISTS idx_fwc_avito_account
    ON funnel_workspace_conversations (business_connection_id, last_message_at DESC NULLS LAST)
    WHERE source_key = 'avito';
