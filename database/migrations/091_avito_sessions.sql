-- Сессия Авито живёт на сервере, а не только в профиле браузера на чужом компьютере.
--
-- Зачем. Браузер обязан работать с домашнего адреса (датацентровый Авито не пускает), но
-- профиль на машине человека — единственная копия входа. Переустановка системы, чистка
-- диска, новый компьютер — и вход надо проходить заново с капчей и SMS. Здесь хранится
-- слепок сессии (куки и localStorage), которым профиль восстанавливается за секунду.
--
-- Содержимое ЗАШИФРОВАНО (Fernet, ключ AVITO_SESSION_KEY в окружении сервера, не в git).
-- Слепок сессии равносилен доступу к аккаунту, поэтому в базе он лежит нечитаемым: дамп
-- базы, попавший не в те руки, не должен отдавать чужой Авито.

CREATE TABLE IF NOT EXISTS avito_sessions (
    slug        text PRIMARY KEY REFERENCES avito_accounts(slug) ON DELETE CASCADE,
    payload     bytea       NOT NULL,
    saved_at    timestamptz NOT NULL DEFAULT now(),
    saved_by    text,
    -- Чей это вход по данным самого Авито. Помогает заметить, что в профиль вошли не тем
    -- аккаунтом: слепок восстановится, а переписка окажется чужой.
    avito_user_id text
);

-- Владелец таблицы обязан совпасть с тем, под кем ходит приложение. Прогнав эту миграцию
-- от postgres, легко получить таблицу суперпользователя — и приложение упрётся в
-- «permission denied for table», уже после того как всё «успешно создалось». Так и вышло
-- 21.08.2026. Поэтому владельца берём у соседней таблицы канала, а не полагаемся на то,
-- под кем миграцию запустили.
DO $$
DECLARE app_owner text;
BEGIN
    SELECT tableowner INTO app_owner FROM pg_tables WHERE tablename = 'avito_accounts';
    IF app_owner IS NOT NULL AND app_owner <> current_user THEN
        EXECUTE format('ALTER TABLE avito_sessions OWNER TO %I', app_owner);
    END IF;
END $$;

COMMENT ON TABLE avito_sessions IS
    'Зашифрованный слепок браузерной сессии Авито: куки и localStorage для восстановления профиля';
COMMENT ON COLUMN avito_sessions.payload IS
    'Fernet-шифртекст JSON storage_state; ключ AVITO_SESSION_KEY только в окружении сервера';
