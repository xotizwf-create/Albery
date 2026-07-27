-- Комментарии по лиду в рабочем окне: свободный текст о клиенте и общении.
-- Пишут и оператор из панели, и агент своим инструментом; каждый комментарий зеркалится
-- в ленту сделки Битрикса. Зеркало отмечается флагом: Битрикс может быть недоступен в
-- момент записи, но комментарий обязан сохраниться у нас — иначе человек потеряет то,
-- что уже написал.
CREATE TABLE IF NOT EXISTS funnel_workspace_lead_notes (
    id                BIGSERIAL PRIMARY KEY,
    conversation_id   BIGINT NOT NULL
                      REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    author_type       TEXT NOT NULL DEFAULT 'operator',
    author_name       TEXT NOT NULL DEFAULT '',
    text              TEXT NOT NULL,
    bitrix_mirrored   BOOLEAN NOT NULL DEFAULT FALSE,
    bitrix_error      TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS funnel_workspace_lead_notes_conversation_idx
    ON funnel_workspace_lead_notes (conversation_id, id DESC);
