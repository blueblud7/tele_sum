-- tele_sum schema (Neon Postgres)
-- 다른 백엔드에서도 공용으로 읽고 쓰는 공유 DB입니다.

CREATE TABLE IF NOT EXISTS channels (
    id          BIGINT PRIMARY KEY,
    username    TEXT,
    title       TEXT,
    type        TEXT,
    selected    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS channels_selected_idx ON channels (selected) WHERE selected;

CREATE TABLE IF NOT EXISTS messages (
    channel_id  BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    message_id  BIGINT NOT NULL,
    posted_at   TIMESTAMPTZ NOT NULL,
    text        TEXT,
    has_media   BOOLEAN NOT NULL DEFAULT FALSE,
    raw         JSONB,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS messages_posted_at_idx ON messages (posted_at DESC);
CREATE INDEX IF NOT EXISTS messages_channel_posted_idx ON messages (channel_id, posted_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
    id           BIGSERIAL PRIMARY KEY,
    channel_id   BIGINT REFERENCES channels(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,
    content      TEXT NOT NULL,
    model        TEXT,
    meta         JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS summaries_kind_created_idx ON summaries (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS summaries_channel_created_idx ON summaries (channel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ingest_state (
    channel_id    BIGINT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    last_seen_id  BIGINT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
