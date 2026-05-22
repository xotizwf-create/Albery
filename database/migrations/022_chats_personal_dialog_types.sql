ALTER TABLE chats
    DROP CONSTRAINT IF EXISTS chats_chat_type_check;

ALTER TABLE chats
    ADD CONSTRAINT chats_chat_type_check
    CHECK (chat_type IN ('group', 'private', 'open', 'user', 'dialog', 'unknown'));
