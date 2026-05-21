-- =====================================================================
-- Employee Analytics System
-- PostgreSQL Schema v1.0
-- Target: PostgreSQL 15+
-- Encoding: UTF-8
-- Timestamps: TIMESTAMPTZ, store UTC at application boundary
-- =====================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "btree_gist";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- =====================================================================
-- Block 1: Team
-- =====================================================================

CREATE TABLE users (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bitrix_user_id           BIGINT NOT NULL UNIQUE,
    full_name                TEXT,
    first_name               TEXT,
    last_name                TEXT,
    second_name              TEXT,
    email                    TEXT,
    phone                    TEXT,
    avatar_url               TEXT,
    work_position            TEXT,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    manager_id               UUID REFERENCES users(id) ON DELETE SET NULL,
    manager_bitrix_user_id   BIGINT,
    raw_json                 JSONB,
    synced_at                TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_manager_id     ON users(manager_id) WHERE manager_id IS NOT NULL;
CREATE INDEX idx_users_is_active      ON users(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_users_full_name_trgm ON users USING GIN (full_name gin_trgm_ops);

CREATE TABLE departments (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bitrix_department_id          BIGINT NOT NULL UNIQUE,
    name                          TEXT NOT NULL,
    parent_id                     UUID REFERENCES departments(id) ON DELETE SET NULL,
    parent_bitrix_department_id   BIGINT,
    head_id                       UUID REFERENCES users(id) ON DELETE SET NULL,
    head_bitrix_user_id           BIGINT,
    raw_json                      JSONB,
    synced_at                     TIMESTAMPTZ,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_departments_parent_id ON departments(parent_id);
CREATE INDEX idx_departments_head_id   ON departments(head_id);

CREATE TABLE user_departments (
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id  UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    is_primary     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, department_id)
);

CREATE INDEX idx_user_departments_department ON user_departments(department_id);
CREATE UNIQUE INDEX idx_user_departments_primary
    ON user_departments(user_id) WHERE is_primary = TRUE;

CREATE TABLE user_hierarchy (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    manager_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL CHECK (relation_type IN
                        ('direct','functional','project','temporary','manual')),
    source          TEXT NOT NULL CHECK (source IN
                        ('bitrix','manual','project','ai')),
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    confidence      NUMERIC(4,3) NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (user_id <> manager_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    EXCLUDE USING gist (
        user_id        WITH =,
        relation_type  WITH =,
        daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[]') WITH &&
    ) WHERE (is_active = TRUE)
);

CREATE INDEX idx_user_hierarchy_user    ON user_hierarchy(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_user_hierarchy_manager ON user_hierarchy(manager_id) WHERE is_active = TRUE;

CREATE TABLE user_goals (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID REFERENCES users(id) ON DELETE CASCADE,
    manager_id             UUID REFERENCES users(id) ON DELETE SET NULL,
    parent_goal_id         UUID REFERENCES user_goals(id) ON DELETE SET NULL,
    goal_title             TEXT NOT NULL,
    goal_text              TEXT,
    goal_level             TEXT NOT NULL CHECK (goal_level IN
                              ('company','department','manager','employee','project')),
    period_type            TEXT NOT NULL CHECK (period_type IN
                              ('day','week','month','quarter','year','project')),
    period_start           DATE NOT NULL,
    period_end             DATE NOT NULL,
    success_metrics        TEXT,
    expected_result        TEXT,
    status                 TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
                              ('draft','active','done','cancelled','archived')),
    source_type            TEXT NOT NULL CHECK (source_type IN
                              ('bitrix','chat','ocr','manual','ai')),
    confidence             NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    is_confirmed           BOOLEAN NOT NULL DEFAULT FALSE,
    confirmed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    CHECK (NOT is_confirmed OR confirmed_at IS NOT NULL)
);

CREATE INDEX idx_user_goals_user   ON user_goals(user_id);
CREATE INDEX idx_user_goals_mgr    ON user_goals(manager_id);
CREATE INDEX idx_user_goals_period ON user_goals(period_type, period_start, period_end);
CREATE INDEX idx_user_goals_status ON user_goals(status) WHERE status IN ('active','draft');
CREATE INDEX idx_user_goals_parent ON user_goals(parent_goal_id);

CREATE TABLE company_profile (
    profile_key            TEXT PRIMARY KEY DEFAULT 'main',
    title                  TEXT NOT NULL DEFAULT 'О компании',
    content                TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (profile_key = 'main')
);

CREATE TABLE company_folders (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id              UUID REFERENCES company_folders(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    content                TEXT NOT NULL DEFAULT '',
    sort_order             INT NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (btrim(name) <> '')
);

CREATE INDEX idx_company_folders_parent ON company_folders(parent_id, sort_order, name);

-- =====================================================================
-- Block 2: Bitrix tasks
-- =====================================================================

CREATE TABLE bitrix_tasks (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bitrix_task_id                BIGINT NOT NULL UNIQUE,
    title                         TEXT NOT NULL,
    description                   TEXT,
    status                        TEXT,
    status_name                   TEXT,
    priority                      TEXT,
    creator_id                    UUID REFERENCES users(id) ON DELETE SET NULL,
    creator_bitrix_user_id        BIGINT,
    responsible_id                UUID REFERENCES users(id) ON DELETE SET NULL,
    responsible_bitrix_user_id    BIGINT,
    deadline_at                   TIMESTAMPTZ,
    created_at_bitrix             TIMESTAMPTZ,
    updated_at_bitrix             TIMESTAMPTZ,
    closed_at_bitrix              TIMESTAMPTZ,
    raw_json                      JSONB,
    synced_at                     TIMESTAMPTZ,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bitrix_tasks_responsible ON bitrix_tasks(responsible_id);
CREATE INDEX idx_bitrix_tasks_creator     ON bitrix_tasks(creator_id);
CREATE INDEX idx_bitrix_tasks_deadline    ON bitrix_tasks(deadline_at) WHERE deadline_at IS NOT NULL;
CREATE INDEX idx_bitrix_tasks_status      ON bitrix_tasks(status);
CREATE INDEX idx_bitrix_tasks_updated     ON bitrix_tasks(updated_at_bitrix DESC);

CREATE TABLE bitrix_task_members (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id           UUID NOT NULL REFERENCES bitrix_tasks(id) ON DELETE CASCADE,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bitrix_user_id    BIGINT,
    role              TEXT NOT NULL CHECK (role IN
                          ('creator','responsible','accomplice','auditor','observer')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, user_id, role)
);

CREATE INDEX idx_btm_user ON bitrix_task_members(user_id);
CREATE INDEX idx_btm_task ON bitrix_task_members(task_id);

CREATE TABLE bitrix_task_sync_runs (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    sync_finished_at       TIMESTAMPTZ,
    period_start           DATE,
    period_end             DATE,
    status                 TEXT NOT NULL CHECK (status IN ('running','success','error','partial')),
    tasks_found_count      INT NOT NULL DEFAULT 0,
    tasks_created_count    INT NOT NULL DEFAULT 0,
    tasks_updated_count    INT NOT NULL DEFAULT 0,
    error_text             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_btsr_started ON bitrix_task_sync_runs(sync_started_at DESC);
CREATE INDEX idx_btsr_status  ON bitrix_task_sync_runs(status);

CREATE TABLE bitrix_task_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES bitrix_tasks(id) ON DELETE CASCADE,
    bitrix_task_id      BIGINT NOT NULL,
    sync_run_id         UUID NOT NULL REFERENCES bitrix_task_sync_runs(id) ON DELETE CASCADE,
    snapshot_date       DATE NOT NULL,
    status              TEXT,
    priority            TEXT,
    responsible_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    deadline_at         TIMESTAMPTZ,
    closed_at_bitrix    TIMESTAMPTZ,
    raw_json            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, sync_run_id)
);

CREATE INDEX idx_bts_task_date ON bitrix_task_snapshots(task_id, snapshot_date DESC);
CREATE INDEX idx_bts_sync_run  ON bitrix_task_snapshots(sync_run_id);
CREATE INDEX idx_bts_date      ON bitrix_task_snapshots(snapshot_date);

-- =====================================================================
-- Block 3: Chats
-- =====================================================================

CREATE TABLE chats (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bitrix_chat_id          BIGINT,
    dialog_id               TEXT NOT NULL UNIQUE,
    chat_title              TEXT,
    chat_type               TEXT NOT NULL DEFAULT 'unknown' CHECK (chat_type IN
                                ('group','private','open','unknown')),
    entity_type             TEXT,
    owner_id                UUID REFERENCES users(id) ON DELETE SET NULL,
    owner_bitrix_user_id    BIGINT,
    last_message_at         TIMESTAMPTZ,
    members_count           INT NOT NULL DEFAULT 0,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    is_excluded             BOOLEAN NOT NULL DEFAULT FALSE,
    excluded_at             TIMESTAMPTZ,
    excluded_reason         TEXT,
    raw_json                JSONB,
    synced_at               TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chats_not_excluded ON chats(is_excluded) WHERE is_excluded = FALSE;
CREATE INDEX idx_chats_owner        ON chats(owner_id);
CREATE INDEX idx_chats_last_msg     ON chats(last_message_at DESC NULLS LAST);

CREATE TABLE chat_members (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id           UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bitrix_user_id    BIGINT,
    role              TEXT NOT NULL DEFAULT 'unknown' CHECK (role IN
                          ('owner','admin','member','unknown')),
    joined_at         TIMESTAMPTZ,
    left_at           TIMESTAMPTZ,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chat_id, user_id)
);

CREATE INDEX idx_chat_members_user ON chat_members(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_chat_members_chat ON chat_members(chat_id) WHERE is_active = TRUE;

CREATE TABLE chat_messages (
    id                       UUID NOT NULL DEFAULT gen_random_uuid(),
    chat_id                  UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    bitrix_message_id        BIGINT NOT NULL,
    author_id                UUID REFERENCES users(id) ON DELETE SET NULL,
    author_bitrix_user_id    BIGINT,
    message_text             TEXT,
    message_date             TIMESTAMPTZ NOT NULL,
    message_day              DATE NOT NULL,
    has_files                BOOLEAN NOT NULL DEFAULT FALSE,
    raw_json                 JSONB,
    synced_at                TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, message_day),
    UNIQUE (bitrix_message_id, message_day)
) PARTITION BY RANGE (message_day);

CREATE TABLE chat_messages_2025_01 PARTITION OF chat_messages FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE chat_messages_2025_02 PARTITION OF chat_messages FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE chat_messages_2025_03 PARTITION OF chat_messages FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE chat_messages_2025_04 PARTITION OF chat_messages FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE chat_messages_2025_05 PARTITION OF chat_messages FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE chat_messages_2025_06 PARTITION OF chat_messages FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE chat_messages_2025_07 PARTITION OF chat_messages FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE chat_messages_2025_08 PARTITION OF chat_messages FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE chat_messages_2025_09 PARTITION OF chat_messages FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE chat_messages_2025_10 PARTITION OF chat_messages FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE chat_messages_2025_11 PARTITION OF chat_messages FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE chat_messages_2025_12 PARTITION OF chat_messages FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE chat_messages_2026_01 PARTITION OF chat_messages FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE chat_messages_2026_02 PARTITION OF chat_messages FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE chat_messages_2026_03 PARTITION OF chat_messages FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE chat_messages_2026_04 PARTITION OF chat_messages FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE chat_messages_2026_05 PARTITION OF chat_messages FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE chat_messages_2026_06 PARTITION OF chat_messages FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE chat_messages_2026_07 PARTITION OF chat_messages FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE chat_messages_2026_08 PARTITION OF chat_messages FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE chat_messages_2026_09 PARTITION OF chat_messages FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE chat_messages_2026_10 PARTITION OF chat_messages FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE chat_messages_2026_11 PARTITION OF chat_messages FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE chat_messages_2026_12 PARTITION OF chat_messages FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TABLE chat_messages_2027_01 PARTITION OF chat_messages FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE chat_messages_2027_02 PARTITION OF chat_messages FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE chat_messages_2027_03 PARTITION OF chat_messages FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE chat_messages_2027_04 PARTITION OF chat_messages FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE chat_messages_2027_05 PARTITION OF chat_messages FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE chat_messages_2027_06 PARTITION OF chat_messages FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE chat_messages_2027_07 PARTITION OF chat_messages FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE chat_messages_2027_08 PARTITION OF chat_messages FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE chat_messages_2027_09 PARTITION OF chat_messages FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE chat_messages_2027_10 PARTITION OF chat_messages FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE chat_messages_2027_11 PARTITION OF chat_messages FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE chat_messages_2027_12 PARTITION OF chat_messages FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE chat_messages_2028_01 PARTITION OF chat_messages FOR VALUES FROM ('2028-01-01') TO ('2028-02-01');
CREATE TABLE chat_messages_2028_02 PARTITION OF chat_messages FOR VALUES FROM ('2028-02-01') TO ('2028-03-01');
CREATE TABLE chat_messages_2028_03 PARTITION OF chat_messages FOR VALUES FROM ('2028-03-01') TO ('2028-04-01');
CREATE TABLE chat_messages_2028_04 PARTITION OF chat_messages FOR VALUES FROM ('2028-04-01') TO ('2028-05-01');
CREATE TABLE chat_messages_2028_05 PARTITION OF chat_messages FOR VALUES FROM ('2028-05-01') TO ('2028-06-01');
CREATE TABLE chat_messages_2028_06 PARTITION OF chat_messages FOR VALUES FROM ('2028-06-01') TO ('2028-07-01');
CREATE TABLE chat_messages_2028_07 PARTITION OF chat_messages FOR VALUES FROM ('2028-07-01') TO ('2028-08-01');
CREATE TABLE chat_messages_2028_08 PARTITION OF chat_messages FOR VALUES FROM ('2028-08-01') TO ('2028-09-01');
CREATE TABLE chat_messages_2028_09 PARTITION OF chat_messages FOR VALUES FROM ('2028-09-01') TO ('2028-10-01');
CREATE TABLE chat_messages_2028_10 PARTITION OF chat_messages FOR VALUES FROM ('2028-10-01') TO ('2028-11-01');
CREATE TABLE chat_messages_2028_11 PARTITION OF chat_messages FOR VALUES FROM ('2028-11-01') TO ('2028-12-01');
CREATE TABLE chat_messages_2028_12 PARTITION OF chat_messages FOR VALUES FROM ('2028-12-01') TO ('2029-01-01');
CREATE TABLE chat_messages_2029_01 PARTITION OF chat_messages FOR VALUES FROM ('2029-01-01') TO ('2029-02-01');
CREATE TABLE chat_messages_2029_02 PARTITION OF chat_messages FOR VALUES FROM ('2029-02-01') TO ('2029-03-01');
CREATE TABLE chat_messages_2029_03 PARTITION OF chat_messages FOR VALUES FROM ('2029-03-01') TO ('2029-04-01');
CREATE TABLE chat_messages_2029_04 PARTITION OF chat_messages FOR VALUES FROM ('2029-04-01') TO ('2029-05-01');
CREATE TABLE chat_messages_2029_05 PARTITION OF chat_messages FOR VALUES FROM ('2029-05-01') TO ('2029-06-01');
CREATE TABLE chat_messages_2029_06 PARTITION OF chat_messages FOR VALUES FROM ('2029-06-01') TO ('2029-07-01');
CREATE TABLE chat_messages_2029_07 PARTITION OF chat_messages FOR VALUES FROM ('2029-07-01') TO ('2029-08-01');
CREATE TABLE chat_messages_2029_08 PARTITION OF chat_messages FOR VALUES FROM ('2029-08-01') TO ('2029-09-01');
CREATE TABLE chat_messages_2029_09 PARTITION OF chat_messages FOR VALUES FROM ('2029-09-01') TO ('2029-10-01');
CREATE TABLE chat_messages_2029_10 PARTITION OF chat_messages FOR VALUES FROM ('2029-10-01') TO ('2029-11-01');
CREATE TABLE chat_messages_2029_11 PARTITION OF chat_messages FOR VALUES FROM ('2029-11-01') TO ('2029-12-01');
CREATE TABLE chat_messages_2029_12 PARTITION OF chat_messages FOR VALUES FROM ('2029-12-01') TO ('2030-01-01');
CREATE TABLE chat_messages_2030_01 PARTITION OF chat_messages FOR VALUES FROM ('2030-01-01') TO ('2030-02-01');
CREATE TABLE chat_messages_2030_02 PARTITION OF chat_messages FOR VALUES FROM ('2030-02-01') TO ('2030-03-01');
CREATE TABLE chat_messages_2030_03 PARTITION OF chat_messages FOR VALUES FROM ('2030-03-01') TO ('2030-04-01');
CREATE TABLE chat_messages_2030_04 PARTITION OF chat_messages FOR VALUES FROM ('2030-04-01') TO ('2030-05-01');
CREATE TABLE chat_messages_2030_05 PARTITION OF chat_messages FOR VALUES FROM ('2030-05-01') TO ('2030-06-01');
CREATE TABLE chat_messages_2030_06 PARTITION OF chat_messages FOR VALUES FROM ('2030-06-01') TO ('2030-07-01');
CREATE TABLE chat_messages_2030_07 PARTITION OF chat_messages FOR VALUES FROM ('2030-07-01') TO ('2030-08-01');
CREATE TABLE chat_messages_2030_08 PARTITION OF chat_messages FOR VALUES FROM ('2030-08-01') TO ('2030-09-01');
CREATE TABLE chat_messages_2030_09 PARTITION OF chat_messages FOR VALUES FROM ('2030-09-01') TO ('2030-10-01');
CREATE TABLE chat_messages_2030_10 PARTITION OF chat_messages FOR VALUES FROM ('2030-10-01') TO ('2030-11-01');
CREATE TABLE chat_messages_2030_11 PARTITION OF chat_messages FOR VALUES FROM ('2030-11-01') TO ('2030-12-01');
CREATE TABLE chat_messages_2030_12 PARTITION OF chat_messages FOR VALUES FROM ('2030-12-01') TO ('2031-01-01');

CREATE INDEX idx_chat_messages_chat_day ON chat_messages(chat_id, message_day DESC);
CREATE INDEX idx_chat_messages_author   ON chat_messages(author_id, message_day);
CREATE INDEX idx_chat_messages_fts      ON chat_messages
    USING GIN (to_tsvector('russian', coalesce(message_text, '')));

CREATE OR REPLACE FUNCTION ensure_chat_messages_partition(target_day DATE) RETURNS void AS $$
DECLARE
    partition_start DATE;
    partition_end DATE;
    partition_name TEXT;
BEGIN
    IF target_day IS NULL THEN
        RAISE EXCEPTION 'message_day cannot be NULL';
    END IF;

    partition_start := date_trunc('month', target_day)::date;
    partition_end := (partition_start + INTERVAL '1 month')::date;
    partition_name := format('chat_messages_%s', to_char(partition_start, 'YYYY_MM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF chat_messages FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        partition_start,
        partition_end
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_chat_messages_ensure_partition() RETURNS TRIGGER AS $$
BEGIN
    PERFORM ensure_chat_messages_partition(NEW.message_day);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_chat_messages_ensure_partition
    BEFORE INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION trg_chat_messages_ensure_partition();

CREATE TABLE chat_message_files (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id        UUID NOT NULL,
    message_day       DATE NOT NULL,
    chat_id           UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    bitrix_file_id    TEXT,
    file_name         TEXT,
    file_type         TEXT CHECK (file_type IN ('image','document','video','other')),
    mime_type         TEXT,
    file_size         BIGINT,
    download_url      TEXT,
    local_path        TEXT,
    is_downloaded     BOOLEAN NOT NULL DEFAULT FALSE,
    downloaded_at     TIMESTAMPTZ,
    raw_json          JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (message_id, message_day)
        REFERENCES chat_messages(id, message_day) ON DELETE CASCADE
);

CREATE INDEX idx_cmf_message ON chat_message_files(message_id, message_day);
CREATE INDEX idx_cmf_chat    ON chat_message_files(chat_id);
CREATE INDEX idx_cmf_type    ON chat_message_files(file_type) WHERE file_type IS NOT NULL;

CREATE TABLE chat_file_ocr (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id         UUID NOT NULL REFERENCES chat_message_files(id) ON DELETE CASCADE,
    ocr_provider    TEXT NOT NULL CHECK (ocr_provider IN
                        ('openai','google_vision','yandex_vision','manual')),
    ocr_text        TEXT,
    ocr_status      TEXT NOT NULL CHECK (ocr_status IN
                        ('pending','success','error','skipped')),
    confidence      NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    error_text      TEXT,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (file_id, ocr_provider)
);

CREATE INDEX idx_chat_file_ocr_status ON chat_file_ocr(ocr_status);
CREATE INDEX idx_chat_file_ocr_fts    ON chat_file_ocr
    USING GIN (to_tsvector('russian', coalesce(ocr_text, '')));

CREATE TABLE chat_exclusions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id               UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    action                TEXT NOT NULL CHECK (action IN ('exclude','include')),
    reason                TEXT,
    created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_exclusions_chat ON chat_exclusions(chat_id, created_at DESC);

-- =====================================================================
-- Block 3b: Zoom calls
-- =====================================================================

CREATE TABLE zoom_calls (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    zoom_account_key           TEXT NOT NULL,
    zoom_user_email            TEXT,
    zoom_meeting_id            BIGINT,
    zoom_uuid                  TEXT NOT NULL UNIQUE,
    topic                      TEXT,
    technical_topic            TEXT,
    start_time_utc             TIMESTAMPTZ NOT NULL,
    start_time_msk             TIMESTAMPTZ NOT NULL,
    end_time_msk               TIMESTAMPTZ,
    call_date                  DATE NOT NULL,
    duration_min               INT,
    timezone                   TEXT,
    share_url                  TEXT,
    analytical_note            TEXT NOT NULL DEFAULT '',
    transcript_text            TEXT NOT NULL DEFAULT '',
    transcript_format          TEXT NOT NULL DEFAULT 'vtt',
    raw_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    synced_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_zoom_calls_tree ON zoom_calls (call_date DESC, start_time_msk DESC);
CREATE INDEX idx_zoom_calls_user ON zoom_calls (zoom_user_email);

CREATE TABLE zoom_call_participants (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id                    UUID NOT NULL REFERENCES zoom_calls(id) ON DELETE CASCADE,
    participant_name           TEXT,
    participant_email          TEXT,
    participant_user_id        TEXT,
    join_time                  TIMESTAMPTZ,
    leave_time                 TIMESTAMPTZ,
    duration_seconds           INT,
    raw_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (call_id, participant_email, participant_name, join_time)
);

CREATE INDEX idx_zoom_call_participants_call ON zoom_call_participants(call_id);

CREATE TABLE zoom_call_transcript_segments (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id                    UUID NOT NULL REFERENCES zoom_calls(id) ON DELETE CASCADE,
    segment_index              INT NOT NULL,
    cue_index                  INT NOT NULL,
    start_offset               TEXT,
    end_offset                 TEXT,
    speaker                    TEXT,
    text                       TEXT NOT NULL,
    raw_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (call_id, segment_index, cue_index)
);

CREATE INDEX idx_zoom_call_transcript_call ON zoom_call_transcript_segments(call_id, segment_index, cue_index);

-- =====================================================================
-- Block 4: AI and prompts
-- =====================================================================

CREATE TABLE ai_prompt_categories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_key    TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    description     TEXT,
    sort_order      INT NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ai_prompts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id           UUID NOT NULL REFERENCES ai_prompt_categories(id) ON DELETE CASCADE,
    prompt_key            TEXT,
    title                 TEXT,
    prompt_text           TEXT NOT NULL,
    version               INT NOT NULL DEFAULT 1,
    is_active             BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_ai_prompts_active_per_category
    ON ai_prompts(category_id) WHERE is_active = TRUE;
CREATE INDEX idx_ai_prompts_category ON ai_prompts(category_id, version DESC);

CREATE TABLE ai_requests (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_type             TEXT NOT NULL CHECK (request_type IN (
                                'image_ocr','chat_daily_analysis','chat_weekly_analysis',
                                'chat_overall_weekly_analysis','user_daily_report',
                                'chat_overall_daily_report','chat_analysis','chat_weekly_report',
                                'chat_overall_weekly_report','owner_daily_report','owner_weekly_report',
                                'weekly_report','monthly_report','quarterly_report',
                                'yearly_report','memory_update','zoom_processing','image_processing')),
    prompt_id                UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    provider                 TEXT NOT NULL CHECK (provider IN
                                ('openai','google','anthropic','yandex')),
    model                    TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN
                                ('pending','success','error','cancelled')),
    started_at               TIMESTAMPTZ,
    finished_at              TIMESTAMPTZ,
    duration_ms              INT,
    input_payload            JSONB,
    prompt_text_snapshot     TEXT,
    response_text            TEXT,
    response_json            JSONB,
    raw_response_json        JSONB,
    input_tokens             INT,
    output_tokens            INT,
    total_tokens             INT,
    estimated_cost           NUMERIC(12,6),
    error_text               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ai_requests_type    ON ai_requests(request_type, created_at DESC);
CREATE INDEX idx_ai_requests_status  ON ai_requests(status) WHERE status IN ('pending','error');
CREATE INDEX idx_ai_requests_created ON ai_requests(created_at DESC);
CREATE INDEX idx_ai_requests_prompt  ON ai_requests(prompt_id);

-- =====================================================================
-- Block 5: Analytics reports
-- =====================================================================

CREATE TABLE user_daily_reports (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date                  DATE NOT NULL,
    version                      INT NOT NULL DEFAULT 1,
    is_current                   BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id                UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                    UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    planned_tasks_count          INT NOT NULL DEFAULT 0,
    done_tasks_count             INT NOT NULL DEFAULT 0,
    overdue_tasks_count          INT NOT NULL DEFAULT 0,
    important_tasks_count        INT NOT NULL DEFAULT 0,
    important_done_count         INT NOT NULL DEFAULT 0,
    without_result_tasks_count   INT NOT NULL DEFAULT 0,
    goal_alignment_score         NUMERIC(5,2) CHECK (goal_alignment_score BETWEEN 0 AND 100),
    task_importance_score        NUMERIC(5,2) CHECK (task_importance_score BETWEEN 0 AND 100),
    execution_score              NUMERIC(5,2) CHECK (execution_score BETWEEN 0 AND 100),
    communication_score          NUMERIC(5,2) CHECK (communication_score BETWEEN 0 AND 100),
    discipline_score             NUMERIC(5,2) CHECK (discipline_score BETWEEN 0 AND 100),
    manager_score                NUMERIC(5,2) CHECK (manager_score BETWEEN 0 AND 100),
    efficiency_score             NUMERIC(5,2) CHECK (efficiency_score BETWEEN 0 AND 100),
    risk_level                   TEXT CHECK (risk_level IN ('low','medium','high')),
    summary                      TEXT,
    problems_summary             TEXT,
    recommendations              TEXT,
    raw_ai_json                  JSONB,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, report_date, version)
);

CREATE UNIQUE INDEX idx_udr_current
    ON user_daily_reports(user_id, report_date) WHERE is_current = TRUE;
CREATE INDEX idx_udr_date    ON user_daily_reports(report_date);
CREATE INDEX idx_udr_user_dt ON user_daily_reports(user_id, report_date DESC) WHERE is_current = TRUE;

CREATE TABLE chat_daily_reports (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id                  UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    report_date              DATE NOT NULL,
    version                  INT NOT NULL DEFAULT 1,
    is_current               BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id            UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    messages_count           INT NOT NULL DEFAULT 0,
    files_count              INT NOT NULL DEFAULT 0,
    ocr_files_count          INT NOT NULL DEFAULT 0,
    extracted_tasks_count    INT NOT NULL DEFAULT 0,
    extracted_goals_count    INT NOT NULL DEFAULT 0,
    extracted_facts_count    INT NOT NULL DEFAULT 0,
    summary                  TEXT,
    risks_summary            TEXT,
    decisions_summary        TEXT,
    raw_ai_json              JSONB,
    status                   TEXT NOT NULL DEFAULT 'done' CHECK (status IN
                                ('done','no_data','error')),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chat_id, report_date, version)
);

CREATE UNIQUE INDEX idx_cdr_current
    ON chat_daily_reports(chat_id, report_date) WHERE is_current = TRUE;
CREATE INDEX idx_cdr_date ON chat_daily_reports(report_date);

CREATE TABLE chat_weekly_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id                    UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id              UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                  UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    days_count                 INT NOT NULL DEFAULT 0,
    daily_reports_count        INT NOT NULL DEFAULT 0,
    messages_count             INT NOT NULL DEFAULT 0,
    goals_created_count        INT NOT NULL DEFAULT 0,
    goal_updates_count         INT NOT NULL DEFAULT 0,
    commitments_count          INT NOT NULL DEFAULT 0,
    results_count              INT NOT NULL DEFAULT 0,
    next_steps_count           INT NOT NULL DEFAULT 0,
    risks_count                INT NOT NULL DEFAULT 0,
    blockers_count             INT NOT NULL DEFAULT 0,
    unresolved_questions_count INT NOT NULL DEFAULT 0,
    done_goal_updates_count    INT NOT NULL DEFAULT 0,
    high_risk_goal_updates_count INT NOT NULL DEFAULT 0,
    summary                    TEXT,
    dynamics_summary           TEXT,
    positives_summary          TEXT,
    problems_summary           TEXT,
    recommendations            TEXT,
    raw_json                   JSONB,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (chat_id, period_start, period_end, version)
);

CREATE UNIQUE INDEX idx_cwr_current
    ON chat_weekly_reports(chat_id, period_start, period_end) WHERE is_current = TRUE;
CREATE INDEX idx_cwr_chat_period ON chat_weekly_reports(chat_id, period_end DESC, period_start DESC);

CREATE TABLE chat_report_items (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_daily_report_id     UUID NOT NULL REFERENCES chat_daily_reports(id) ON DELETE CASCADE,
    chat_id                  UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    report_date              DATE NOT NULL,
    item_type                TEXT NOT NULL CHECK (item_type IN
                                ('decision','risk','blocker','commitment',
                                 'question','unanswered_question','goal',
                                 'action','bitrix_reference','result',
                                 'next_step','note','previous_day_task')),
    item_text                TEXT NOT NULL,
    user_id                  UUID REFERENCES users(id) ON DELETE SET NULL,
    bitrix_task_id           UUID REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    confidence               NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence_message_ids     BIGINT[],
    raw_json                 JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cri_report ON chat_report_items(chat_daily_report_id);
CREATE INDEX idx_cri_chat_date ON chat_report_items(chat_id, report_date DESC);
CREATE INDEX idx_cri_type ON chat_report_items(item_type);
CREATE INDEX idx_cri_user ON chat_report_items(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_cri_bitrix_task ON chat_report_items(bitrix_task_id) WHERE bitrix_task_id IS NOT NULL;

CREATE TABLE goal_progress_events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id                  UUID NOT NULL REFERENCES user_goals(id) ON DELETE CASCADE,
    chat_daily_report_id     UUID REFERENCES chat_daily_reports(id) ON DELETE CASCADE,
    chat_id                  UUID REFERENCES chats(id) ON DELETE CASCADE,
    report_date              DATE NOT NULL,
    status_before            TEXT CHECK (status_before IN
                              ('draft','active','done','cancelled','archived')),
    status_after             TEXT CHECK (status_after IN
                              ('draft','active','done','cancelled','archived')),
    progress_text            TEXT NOT NULL,
    progress_percent         NUMERIC(5,2) CHECK (progress_percent BETWEEN 0 AND 100),
    metric_value             TEXT,
    risk_level               TEXT CHECK (risk_level IN ('low','medium','high','unknown')),
    confidence               NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence_message_ids     BIGINT[],
    raw_json                 JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_gpe_goal_date ON goal_progress_events(goal_id, report_date DESC, created_at DESC);
CREATE INDEX idx_gpe_chat_report ON goal_progress_events(chat_daily_report_id) WHERE chat_daily_report_id IS NOT NULL;
CREATE INDEX idx_gpe_chat_date ON goal_progress_events(chat_id, report_date DESC) WHERE chat_id IS NOT NULL;

CREATE TABLE chat_overall_daily_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date                DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    chats_count                INT NOT NULL DEFAULT 0,
    messages_count             INT NOT NULL DEFAULT 0,
    goals_created_count        INT NOT NULL DEFAULT 0,
    goal_updates_count         INT NOT NULL DEFAULT 0,
    commitments_count          INT NOT NULL DEFAULT 0,
    results_count              INT NOT NULL DEFAULT 0,
    next_steps_count           INT NOT NULL DEFAULT 0,
    risks_count                INT NOT NULL DEFAULT 0,
    blockers_count             INT NOT NULL DEFAULT 0,
    unresolved_questions_count INT NOT NULL DEFAULT 0,
    done_goal_updates_count    INT NOT NULL DEFAULT 0,
    high_risk_goal_updates_count INT NOT NULL DEFAULT 0,
    summary                    TEXT,
    dynamics_summary           TEXT,
    positives_summary          TEXT,
    problems_summary           TEXT,
    raw_json                   JSONB,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_date, version)
);

CREATE UNIQUE INDEX idx_codr_current ON chat_overall_daily_reports(report_date) WHERE is_current = TRUE;
CREATE INDEX idx_codr_date ON chat_overall_daily_reports(report_date DESC);

CREATE TABLE chat_overall_weekly_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id              UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                  UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    days_count                 INT NOT NULL DEFAULT 0,
    chats_count                INT NOT NULL DEFAULT 0,
    daily_reports_count        INT NOT NULL DEFAULT 0,
    messages_count             INT NOT NULL DEFAULT 0,
    goals_created_count        INT NOT NULL DEFAULT 0,
    goal_updates_count         INT NOT NULL DEFAULT 0,
    commitments_count          INT NOT NULL DEFAULT 0,
    results_count              INT NOT NULL DEFAULT 0,
    next_steps_count           INT NOT NULL DEFAULT 0,
    risks_count                INT NOT NULL DEFAULT 0,
    blockers_count             INT NOT NULL DEFAULT 0,
    unresolved_questions_count INT NOT NULL DEFAULT 0,
    done_goal_updates_count    INT NOT NULL DEFAULT 0,
    high_risk_goal_updates_count INT NOT NULL DEFAULT 0,
    summary                    TEXT,
    dynamics_summary           TEXT,
    positives_summary          TEXT,
    problems_summary           TEXT,
    recommendations            TEXT,
    raw_json                   JSONB,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (period_start, period_end, version)
);

CREATE UNIQUE INDEX idx_cowr_current ON chat_overall_weekly_reports(period_start, period_end) WHERE is_current = TRUE;
CREATE INDEX idx_cowr_period ON chat_overall_weekly_reports(period_start DESC, period_end DESC);

CREATE TABLE owner_daily_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date                DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id              UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                  UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    summary                    TEXT,
    dynamics_summary           TEXT,
    risks_summary              TEXT,
    recommendations            TEXT,
    report_text                TEXT,
    raw_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_date, version)
);

CREATE UNIQUE INDEX idx_owdr_current ON owner_daily_reports(report_date) WHERE is_current = TRUE;
CREATE INDEX idx_owdr_date ON owner_daily_reports(report_date DESC);

CREATE TABLE owner_weekly_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id              UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                  UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    summary                    TEXT,
    dynamics_summary           TEXT,
    risks_summary              TEXT,
    recommendations            TEXT,
    report_text                TEXT,
    raw_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (period_start, period_end, version)
);

CREATE UNIQUE INDEX idx_owwr_current ON owner_weekly_reports(period_start, period_end) WHERE is_current = TRUE;
CREATE INDEX idx_owwr_period ON owner_weekly_reports(period_start DESC, period_end DESC);

CREATE TABLE owner_manager_recommendations (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_scope               TEXT NOT NULL CHECK (source_scope IN ('owner_daily','owner_weekly','owner_monthly')),
    owner_daily_report_id      UUID REFERENCES owner_daily_reports(id) ON DELETE CASCADE,
    owner_weekly_report_id     UUID REFERENCES owner_weekly_reports(id) ON DELETE CASCADE,
    report_date                DATE,
    period_start               DATE,
    period_end                 DATE,
    manager_user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    manager_bitrix_user_id     BIGINT,
    employee_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
    employee_bitrix_user_id    BIGINT,
    recommendation_type        TEXT NOT NULL DEFAULT 'action' CHECK (recommendation_type IN ('action','followup','risk','goal','task')),
    priority                   TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
    subject                    TEXT,
    recommendation_text        TEXT NOT NULL,
    due_date                   DATE,
    bitrix_task_id             UUID REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    bitrix_task_external_id    BIGINT,
    source_chat_id             UUID REFERENCES chats(id) ON DELETE SET NULL,
    source_goal_id             UUID REFERENCES user_goals(id) ON DELETE SET NULL,
    source_item_id             UUID REFERENCES chat_report_items(id) ON DELETE SET NULL,
    source_payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_action            TEXT,
    response_due_at            TIMESTAMPTZ,
    execution_due_at           TIMESTAMPTZ,
    feedback_chat_id           UUID REFERENCES chats(id) ON DELETE SET NULL,
    feedback_dialog_id         TEXT,
    current_interpretation     JSONB NOT NULL DEFAULT '{}'::jsonb,
    manager_review_required    BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at                    TIMESTAMPTZ,
    last_response_at           TIMESTAMPTZ,
    closed_at                  TIMESTAMPTZ,
    status                     TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                                'new','draft','queued','sent','seen','acked','accepted',
                                'in_progress','needs_clarification','disagreed','delegated',
                                'done','rejected','no_response','overdue',
                                'requires_manager_review','cancelled','error')),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (source_scope = 'owner_daily' AND owner_daily_report_id IS NOT NULL)
        OR (source_scope = 'owner_weekly' AND owner_weekly_report_id IS NOT NULL)
        OR (source_scope = 'owner_monthly')
    )
);

CREATE INDEX idx_omr_manager_status ON owner_manager_recommendations(manager_user_id, status, priority, created_at DESC);
CREATE INDEX idx_omr_daily_report ON owner_manager_recommendations(owner_daily_report_id) WHERE owner_daily_report_id IS NOT NULL;
CREATE INDEX idx_omr_weekly_report ON owner_manager_recommendations(owner_weekly_report_id) WHERE owner_weekly_report_id IS NOT NULL;
CREATE INDEX idx_omr_due_date ON owner_manager_recommendations(due_date) WHERE due_date IS NOT NULL;
CREATE INDEX idx_omr_feedback_chat ON owner_manager_recommendations(feedback_chat_id, status, created_at DESC) WHERE feedback_chat_id IS NOT NULL;
CREATE INDEX idx_omr_review_required ON owner_manager_recommendations(manager_review_required, updated_at DESC) WHERE manager_review_required = TRUE;
CREATE INDEX idx_omr_response_due ON owner_manager_recommendations(response_due_at) WHERE response_due_at IS NOT NULL;

CREATE TABLE owner_recommendation_dispatches (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id          UUID NOT NULL REFERENCES owner_manager_recommendations(id) ON DELETE CASCADE,
    channel                    TEXT NOT NULL CHECK (channel IN ('bitrix_task_comment','bitrix_im','bitrix_notification','bitrix_task_create','manual')),
    status                     TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','delivered','error','cancelled')),
    bitrix_entity_type         TEXT,
    bitrix_entity_id           BIGINT,
    bitrix_message_id          BIGINT,
    payload                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    sent_by_user_id            UUID REFERENCES users(id) ON DELETE SET NULL,
    sent_at                    TIMESTAMPTZ,
    error_text                 TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ord_recommendation ON owner_recommendation_dispatches(recommendation_id, created_at DESC);
CREATE INDEX idx_ord_status ON owner_recommendation_dispatches(status, created_at DESC);

CREATE TABLE owner_recommendation_events (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id          UUID NOT NULL REFERENCES owner_manager_recommendations(id) ON DELETE CASCADE,
    event_type                 TEXT NOT NULL CHECK (event_type IN
                                ('created','sent','delivered','seen','employee_replied',
                                 'ai_interpreted','status_changed','manager_reviewed',
                                 'task_created','closed','source_found')),
    author_type                TEXT NOT NULL DEFAULT 'system' CHECK (author_type IN ('system','ai','manager','employee')),
    author_user_id             UUID REFERENCES users(id) ON DELETE SET NULL,
    author_bitrix_user_id      BIGINT,
    chat_id                    UUID REFERENCES chats(id) ON DELETE SET NULL,
    dialog_id                  TEXT,
    chat_message_id            UUID,
    chat_message_day           DATE,
    bitrix_message_id          BIGINT,
    bitrix_task_id             UUID REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    bitrix_task_external_id    BIGINT,
    zoom_call_id               UUID REFERENCES zoom_calls(id) ON DELETE SET NULL,
    old_status                 TEXT,
    new_status                 TEXT,
    event_text                 TEXT,
    interpretation             JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ore_recommendation_event_at ON owner_recommendation_events(recommendation_id, event_at DESC);
CREATE INDEX idx_ore_status ON owner_recommendation_events(new_status, event_at DESC) WHERE new_status IS NOT NULL;
CREATE INDEX idx_ore_chat_day ON owner_recommendation_events(chat_id, chat_message_day, event_at DESC) WHERE chat_id IS NOT NULL;
CREATE INDEX idx_ore_bitrix_message ON owner_recommendation_events(bitrix_message_id, chat_message_day) WHERE bitrix_message_id IS NOT NULL;

CREATE TABLE user_period_reports (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_type                  TEXT NOT NULL CHECK (period_type IN
                                    ('week','month','quarter','year')),
    period_start                 DATE NOT NULL,
    period_end                   DATE NOT NULL,
    version                      INT NOT NULL DEFAULT 1,
    is_current                   BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id                UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                    UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_tasks_count             INT NOT NULL DEFAULT 0,
    overdue_tasks_count          INT NOT NULL DEFAULT 0,
    important_done_count         INT NOT NULL DEFAULT 0,
    avg_goal_alignment_score     NUMERIC(5,2) CHECK (avg_goal_alignment_score BETWEEN 0 AND 100),
    avg_execution_score          NUMERIC(5,2) CHECK (avg_execution_score BETWEEN 0 AND 100),
    avg_communication_score      NUMERIC(5,2) CHECK (avg_communication_score BETWEEN 0 AND 100),
    avg_discipline_score         NUMERIC(5,2) CHECK (avg_discipline_score BETWEEN 0 AND 100),
    avg_manager_score            NUMERIC(5,2) CHECK (avg_manager_score BETWEEN 0 AND 100),
    avg_efficiency_score         NUMERIC(5,2) CHECK (avg_efficiency_score BETWEEN 0 AND 100),
    trend_summary                TEXT,
    strengths_summary            TEXT,
    weaknesses_summary           TEXT,
    risks_summary                TEXT,
    recommendations              TEXT,
    summary                      TEXT,
    raw_ai_json                  JSONB,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (user_id, period_type, period_start, period_end, version)
);

CREATE UNIQUE INDEX idx_upr_current
    ON user_period_reports(user_id, period_type, period_start, period_end)
    WHERE is_current = TRUE;
CREATE INDEX idx_upr_user_period ON user_period_reports(user_id, period_type, period_start DESC);

CREATE TABLE user_dynamics (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_type       TEXT NOT NULL CHECK (period_type IN
                          ('day','week','month','quarter','year')),
    period_start      DATE NOT NULL,
    period_end        DATE NOT NULL,
    metric_name       TEXT NOT NULL,
    current_value     NUMERIC(12,4),
    previous_value    NUMERIC(12,4),
    delta_value       NUMERIC(12,4),
    delta_percent     NUMERIC(8,2),
    trend             TEXT CHECK (trend IN ('improved','worsened','stable')),
    interpretation    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, period_type, period_start, period_end, metric_name),
    CHECK (period_end >= period_start)
);

CREATE INDEX idx_user_dynamics_user_period
    ON user_dynamics(user_id, period_type, period_start DESC);
CREATE INDEX idx_user_dynamics_metric ON user_dynamics(metric_name);

CREATE TABLE user_memory (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_type                 TEXT NOT NULL CHECK (memory_type IN
                                   ('strength','weakness','risk','recommendation',
                                    'behavior','performance_note','management_note',
                                    'goal_context')),
    title                       TEXT,
    content                     TEXT NOT NULL,
    content_hash                TEXT NOT NULL,
    evidence_count              INT NOT NULL DEFAULT 1,
    first_seen_at               DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen_at                DATE NOT NULL DEFAULT CURRENT_DATE,
    last_evidence_daily_id      UUID REFERENCES user_daily_reports(id) ON DELETE SET NULL,
    last_evidence_period_id     UUID REFERENCES user_period_reports(id) ON DELETE SET NULL,
    source_daily_report_id      UUID REFERENCES user_daily_reports(id) ON DELETE SET NULL,
    source_period_report_id     UUID REFERENCES user_period_reports(id) ON DELETE SET NULL,
    trend                       TEXT CHECK (trend IN
                                   ('improving','worsening','stable','unknown')),
    confidence                  NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    raw_ai_json                 JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, memory_type, content_hash)
);

CREATE INDEX idx_user_memory_user   ON user_memory(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_user_memory_type   ON user_memory(memory_type) WHERE is_active = TRUE;
CREATE INDEX idx_user_memory_active ON user_memory(user_id, memory_type) WHERE is_active = TRUE;

CREATE TABLE ai_request_artifacts (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ai_request_id                UUID NOT NULL REFERENCES ai_requests(id) ON DELETE CASCADE,
    chat_id                      UUID REFERENCES chats(id) ON DELETE CASCADE,
    chat_message_id              UUID,
    chat_message_day             DATE,
    file_id                      UUID REFERENCES chat_message_files(id) ON DELETE CASCADE,
    ocr_id                       UUID REFERENCES chat_file_ocr(id) ON DELETE CASCADE,
    user_id                      UUID REFERENCES users(id) ON DELETE CASCADE,
    bitrix_task_id               UUID REFERENCES bitrix_tasks(id) ON DELETE CASCADE,
    goal_id                      UUID REFERENCES user_goals(id) ON DELETE CASCADE,
    chat_daily_report_id         UUID REFERENCES chat_daily_reports(id) ON DELETE CASCADE,
    user_daily_report_id         UUID REFERENCES user_daily_reports(id) ON DELETE CASCADE,
    user_period_report_id        UUID REFERENCES user_period_reports(id) ON DELETE CASCADE,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (chat_message_id, chat_message_day)
        REFERENCES chat_messages(id, message_day) ON DELETE CASCADE,
    CONSTRAINT ara_at_least_one CHECK (
        num_nonnulls(chat_id, chat_message_id, file_id, ocr_id, user_id,
                     bitrix_task_id, goal_id,
                     chat_daily_report_id, user_daily_report_id,
                     user_period_report_id) >= 1
    )
);

CREATE INDEX idx_ara_request   ON ai_request_artifacts(ai_request_id);
CREATE INDEX idx_ara_user      ON ai_request_artifacts(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_ara_chat      ON ai_request_artifacts(chat_id) WHERE chat_id IS NOT NULL;
CREATE INDEX idx_ara_udr       ON ai_request_artifacts(user_daily_report_id) WHERE user_daily_report_id IS NOT NULL;
CREATE INDEX idx_ara_upr       ON ai_request_artifacts(user_period_report_id) WHERE user_period_report_id IS NOT NULL;

CREATE TABLE audit_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    action        TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    entity_id     UUID,
    before_json   JSONB,
    after_json    JSONB,
    reason        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_entity  ON audit_log(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_actor   ON audit_log(actor_id, created_at DESC);
CREATE INDEX idx_audit_action  ON audit_log(action, created_at DESC);
CREATE INDEX idx_audit_created ON audit_log(created_at DESC);

-- =====================================================================
-- Block 6: Triggers
-- =====================================================================

CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    tbl TEXT;
    tables TEXT[] := ARRAY[
        'users','departments','user_hierarchy','user_goals',
        'bitrix_tasks','chats','chat_members','chat_message_files',
        'chat_file_ocr','ai_prompt_categories','chat_daily_reports','chat_weekly_reports',
        'chat_overall_daily_reports','chat_overall_weekly_reports',
        'owner_daily_reports','owner_weekly_reports',
        'owner_manager_recommendations','owner_recommendation_dispatches',
        'user_daily_reports','user_period_reports','user_memory'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at()',
             tbl, tbl);
    END LOOP;
END$$;

CREATE OR REPLACE FUNCTION trg_switch_is_current_user_daily() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE user_daily_reports
            SET is_current = FALSE
            WHERE user_id = NEW.user_id
              AND report_date = NEW.report_date
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_udr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON user_daily_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_user_daily();

CREATE OR REPLACE FUNCTION trg_switch_is_current_user_period() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE user_period_reports
            SET is_current = FALSE
            WHERE user_id = NEW.user_id
              AND period_type = NEW.period_type
              AND period_start = NEW.period_start
              AND period_end = NEW.period_end
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_upr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON user_period_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_user_period();

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_daily() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_daily_reports
            SET is_current = FALSE
            WHERE chat_id = NEW.chat_id
              AND report_date = NEW.report_date
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cdr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_daily_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_daily();

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_weekly() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_weekly_reports
            SET is_current = FALSE
            WHERE chat_id = NEW.chat_id
              AND period_start = NEW.period_start
              AND period_end = NEW.period_end
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cwr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_weekly_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_weekly();

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_overall_daily() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_overall_daily_reports
            SET is_current = FALSE
            WHERE report_date = NEW.report_date
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_codr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_overall_daily_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_overall_daily();

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_overall_weekly() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_overall_weekly_reports
            SET is_current = FALSE
            WHERE period_start = NEW.period_start
              AND period_end = NEW.period_end
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cowr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_overall_weekly_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_overall_weekly();

CREATE OR REPLACE FUNCTION trg_switch_active_prompt() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_active THEN
        UPDATE ai_prompts
            SET is_active = FALSE
            WHERE category_id = NEW.category_id
              AND id <> NEW.id
              AND is_active = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ai_prompts_switch_active
    BEFORE INSERT OR UPDATE OF is_active ON ai_prompts
    FOR EACH ROW WHEN (NEW.is_active = TRUE)
    EXECUTE FUNCTION trg_switch_active_prompt();

CREATE OR REPLACE FUNCTION trg_track_manager_history() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.manager_id IS NOT DISTINCT FROM OLD.manager_id THEN
        RETURN NEW;
    END IF;

    UPDATE user_hierarchy
        SET valid_to = CURRENT_DATE,
            is_active = FALSE,
            updated_at = now()
        WHERE user_id = NEW.id
          AND relation_type = 'direct'
          AND is_active = TRUE;

    IF NEW.manager_id IS NOT NULL THEN
        INSERT INTO user_hierarchy (
            user_id, manager_id, relation_type, source,
            valid_from, is_active, confidence
        ) VALUES (
            NEW.id, NEW.manager_id, 'direct', 'bitrix',
            CURRENT_DATE, TRUE, 1.0
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_manager_history
    AFTER INSERT OR UPDATE OF manager_id ON users
    FOR EACH ROW EXECUTE FUNCTION trg_track_manager_history();

CREATE OR REPLACE FUNCTION trg_audit_chat_exclusion() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_excluded IS DISTINCT FROM OLD.is_excluded THEN
        INSERT INTO audit_log (action, entity_type, entity_id, before_json, after_json)
        VALUES (
            CASE WHEN NEW.is_excluded THEN 'chat_excluded' ELSE 'chat_included' END,
            'chat', NEW.id,
            jsonb_build_object('is_excluded', OLD.is_excluded),
            jsonb_build_object('is_excluded', NEW.is_excluded, 'reason', NEW.excluded_reason)
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_chats_audit_exclusion
    AFTER UPDATE OF is_excluded ON chats
    FOR EACH ROW EXECUTE FUNCTION trg_audit_chat_exclusion();

-- =====================================================================
-- Block 7: Views and seed data
-- =====================================================================

CREATE OR REPLACE VIEW v_user_daily_reports_current AS
SELECT * FROM user_daily_reports WHERE is_current = TRUE;

CREATE OR REPLACE VIEW v_user_period_reports_current AS
SELECT * FROM user_period_reports WHERE is_current = TRUE;

CREATE OR REPLACE VIEW v_chat_daily_reports_current AS
SELECT * FROM chat_daily_reports WHERE is_current = TRUE;

CREATE OR REPLACE VIEW v_chat_weekly_reports_current AS
SELECT * FROM chat_weekly_reports WHERE is_current = TRUE;

INSERT INTO ai_prompt_categories (category_key, title, description, sort_order)
VALUES
    ('daily_report', 'Daily report', 'Employee daily report prompt', 10),
    ('weekly_report', 'Weekly report', 'Employee weekly report prompt', 20),
    ('monthly_report', 'Monthly report', 'Employee monthly report prompt', 30),
    ('quarterly_report', 'Quarterly report', 'Employee quarterly report prompt', 40),
    ('yearly_report', 'Yearly report', 'Employee yearly report prompt', 50),
    ('chat_analysis', 'Chat analysis', 'Daily chat analysis prompt', 60),
    ('image_processing', 'Image processing', 'Image OCR prompt', 70);

COMMIT;
