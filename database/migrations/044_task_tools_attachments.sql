-- 044: Attachment store + task-comment mention dedupe.
-- Fully idempotent (IF NOT EXISTS everywhere). Registered in ALWAYS_APPLY_MIGRATIONS.
--
-- bitrix_bot_attachments: every file a user sends the bot is captured here so that
--   (a) the FULL extracted text is available to the agent on demand (no 12k truncation),
--   (b) the raw bytes can be re-attached to a task / comment / result later.
-- The short random `token` is the only handle: it is injected into the agent's prompt for
-- the dialog that received the file, and the agent passes it to get_attachment_text /
-- attach_files_to_task / add_bitrix_task_comment(attachment_ids=...).

CREATE TABLE IF NOT EXISTS bitrix_bot_attachments (
    token          text PRIMARY KEY,
    agent_slug     text,
    dialog_id      text,
    bitrix_user_id integer,
    file_name      text NOT NULL,
    ext            text,
    kind           text,            -- 'image' | 'document'
    mime           text,
    byte_size      integer,
    char_len       integer,
    extracted_text text,            -- full extracted text (documents) / OCR text (images)
    file_path      text,            -- path to the stored raw bytes on the box (for re-upload)
    bitrix_disk_id integer,         -- lazily-filled: disk file id after re-upload to webhook storage
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bot_attachments_dialog
    ON bitrix_bot_attachments (agent_slug, dialog_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bot_attachments_created
    ON bitrix_bot_attachments (created_at DESC);

-- Dedupe + loop-guard for the task-comment mention handler. A row means "this comment was
-- already seen" — the OnTaskCommentAdd webhook fires for every comment company-wide, so we
-- INSERT ... ON CONFLICT DO NOTHING keyed by the Bitrix comment id and only act on first sight.
CREATE TABLE IF NOT EXISTS bitrix_task_comment_seen (
    comment_id  bigint PRIMARY KEY,
    task_id     bigint,
    agent_slug  text,
    author_id   integer,
    handled     boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_comment_seen_created
    ON bitrix_task_comment_seen (created_at DESC);
