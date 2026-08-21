-- Заявка на вход: кнопка в кабинете просит воркер открыть окно браузера НА СВОЁМ устройстве.
--
-- Кабинет — страница на сервере, и открыть браузер на компьютере человека он не может.
-- А войти в Авито можно только с домашнего адреса: датацентровый Авито не пускает, вход из
-- другой страны может закрыть аккаунт. Поэтому кабинет не «открывает браузер», а оставляет
-- заявку; воркер, который и так работает на нужной машине, видит её на ближайшем обходе и
-- открывает окно там.
--
-- Заявка снимается САМА, когда вход подтверждён. Снимать её по факту «воркер увидел» нельзя:
-- человек мог не дойти до компьютера, и заявка обязана дожить до настоящего входа.

ALTER TABLE avito_accounts
    ADD COLUMN IF NOT EXISTS login_requested_at timestamptz,
    ADD COLUMN IF NOT EXISTS login_requested_by text;

COMMENT ON COLUMN avito_accounts.login_requested_at IS
    'Кабинет попросил воркер открыть окно входа; снимается автоматически при session_status=ok';

-- Владельца выравниваем по соседней таблице канала: миграция, запущенная от postgres, иначе
-- оставляет приложению «permission denied» уже после успешного «создано» (обожглись 21.08.2026).
DO $$
DECLARE app_owner text;
BEGIN
    SELECT tableowner INTO app_owner FROM pg_tables WHERE tablename = 'avito_accounts';
    IF app_owner IS NOT NULL AND app_owner <> current_user THEN
        EXECUTE format('ALTER TABLE avito_accounts OWNER TO %I', app_owner);
    END IF;
END $$;
