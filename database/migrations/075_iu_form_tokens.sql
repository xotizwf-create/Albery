-- 075_iu_form_tokens.sql
-- Idempotent. Персональная одноразовая ссылка на анкету ИУ.
--
-- Why: анкету принимает CRM-форма Битрикса, и сделку из неё создаёт САМ Битрикс — наш код в
-- этот момент не спрашивают. Поэтому клиент, уже заведённый из Telegram, получал вторую
-- карточку. Единственный канал, по которому форма пропускает что-то в сделку, — пять
-- utm-меток (проверено разбором виджета и живой заявкой 29.07.2026, сделка 264:
-- UTM_CONTENT доехал). Кладём туда одноразовый токен, а не сам telegram_id: ссылку пересылают
-- и сохраняют, и номер аккаунта в открытом адресе светиться не должен.
--
-- Токен привязан к человеку, живёт ограниченное время и гаснет по факту ЗАПОЛНЕНИЯ анкеты,
-- а не по факту перехода: владелец 29.07.2026 — «перешёл с телефона, не заполнил, перешёл с
-- компа, заполнил — действие ссылки истекло».
CREATE TABLE IF NOT EXISTS iu_form_tokens (
    token           text PRIMARY KEY,
    telegram_id     bigint NOT NULL,
    conversation_id bigint,
    deal_id         bigint,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    opened_at       timestamptz,          -- первый переход по ссылке
    open_count      integer NOT NULL DEFAULT 0,
    used_at         timestamptz,          -- анкета заполнена: токен сгорел
    used_deal_id    bigint                -- сделка, которую создала эта заявка
);

-- Живой токен человека ищется на каждое нажатие кнопки: повторное нажатие обязано отдавать
-- ТУ ЖЕ ссылку, иначе одноразовость превращается в фикцию — старые ссылки оставались бы
-- рабочими, и «анкета уже заполнена» никогда бы не срабатывало.
CREATE INDEX IF NOT EXISTS idx_iu_form_tokens_live
    ON iu_form_tokens (telegram_id, expires_at)
    WHERE used_at IS NULL;

-- Вотчер идёт от метки сделки к человеку.
CREATE INDEX IF NOT EXISTS idx_iu_form_tokens_used_deal
    ON iu_form_tokens (used_deal_id)
    WHERE used_deal_id IS NOT NULL;
