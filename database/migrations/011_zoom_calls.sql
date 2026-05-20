CREATE TABLE IF NOT EXISTS zoom_calls (
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

CREATE INDEX IF NOT EXISTS idx_zoom_calls_tree ON zoom_calls (call_date DESC, start_time_msk DESC);
CREATE INDEX IF NOT EXISTS idx_zoom_calls_user ON zoom_calls (zoom_user_email);

CREATE TABLE IF NOT EXISTS zoom_call_participants (
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

CREATE INDEX IF NOT EXISTS idx_zoom_call_participants_call ON zoom_call_participants(call_id);

CREATE TABLE IF NOT EXISTS zoom_call_transcript_segments (
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

CREATE INDEX IF NOT EXISTS idx_zoom_call_transcript_call ON zoom_call_transcript_segments(call_id, segment_index, cue_index);
