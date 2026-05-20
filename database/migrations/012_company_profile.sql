CREATE TABLE IF NOT EXISTS company_profile (
    profile_key text PRIMARY KEY DEFAULT 'main',
    title text NOT NULL DEFAULT 'О компании',
    content text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (profile_key = 'main')
);

INSERT INTO company_profile (profile_key, title, content)
VALUES ('main', 'О компании', '')
ON CONFLICT (profile_key) DO NOTHING;
