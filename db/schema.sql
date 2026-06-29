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

-- ── 파생 신호 사전계산 (tele_signals.py가 채움, reportportal 대시보드가 읽음) ──
-- 매 윈도우(window_days)마다 DELETE 후 재삽입하는 방식이라 PK만 있으면 충분하다.

-- ② 종목 회자수: 윈도우별 종목이 등장한 '메시지 수'
CREATE TABLE IF NOT EXISTS tele_stock_mentions (
    stock_name     TEXT NOT NULL,
    window_days    INTEGER NOT NULL,
    mention_count  INTEGER NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_name, window_days)
);

-- ③ 테마: 윈도우별 상위 토큰(종목명 제외) 빈도 랭킹
CREATE TABLE IF NOT EXISTS tele_themes (
    window_days  INTEGER NOT NULL,
    word         TEXT NOT NULL,
    score        INTEGER NOT NULL,
    rank         INTEGER NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (window_days, word)
);

-- ④ 총량: 윈도우별 메시지 건수
CREATE TABLE IF NOT EXISTS tele_kpi (
    window_days  INTEGER NOT NULL,
    msg_count    INTEGER NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (window_days)
);

-- ── 일/주/월 TOP10 빈도+뷰 브리프 (top_signals.py가 채움) ──
-- period(daily/weekly/monthly) × dimension(stock/sector)별 상위 N개 종목·섹터를
-- 텔레그램 회자수 + 리포트 빈도 + LLM 1~2줄 '시각'으로 적재. reportportal이 읽는다.
CREATE TABLE IF NOT EXISTS top_signal_briefs (
    id           BIGSERIAL PRIMARY KEY,
    period       TEXT NOT NULL,          -- daily | weekly | monthly
    dimension    TEXT NOT NULL,          -- stock | sector
    rank         INTEGER NOT NULL,
    name         TEXT NOT NULL,
    tele_count   INTEGER NOT NULL DEFAULT 0,
    report_count INTEGER NOT NULL DEFAULT 0,
    view         TEXT,
    as_of        DATE NOT NULL,
    window_days  INTEGER NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (period, dimension, rank, as_of)
);

CREATE INDEX IF NOT EXISTS top_signal_briefs_lookup_idx
    ON top_signal_briefs (period, dimension, as_of DESC, rank);
