CREATE TABLE IF NOT EXISTS site_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    discord_user_id TEXT UNIQUE,
    discord_username TEXT,
    discord_avatar TEXT,
    discord_email TEXT,
    verified_discord BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_links (
    id BIGSERIAL PRIMARY KEY,
    site_user_id BIGINT UNIQUE NOT NULL REFERENCES site_users(id) ON DELETE CASCADE,
    discord_id TEXT,
    discord_username TEXT,
    steam_id TEXT,
    steam_name TEXT,
    link_status TEXT NOT NULL DEFAULT 'pending',
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    auto_approved BOOLEAN NOT NULL DEFAULT FALSE,
    last_linked_at TIMESTAMPTZ,
    relink_available_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS riders (
    id BIGSERIAL PRIMARY KEY,
    discord_id TEXT UNIQUE,
    discord_user_id TEXT,
    discord_username TEXT,
    mxb_name TEXT,
    guid TEXT UNIQUE,
    steam_id TEXT,
    class_name TEXT,
    is_linked BOOLEAN NOT NULL DEFAULT FALSE,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    auto_approved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    track_name TEXT NOT NULL,
    series TEXT NOT NULL DEFAULT 'MXGP',
    class_name TEXT NOT NULL,
    race_stage TEXT NOT NULL DEFAULT 'main',
    status TEXT NOT NULL DEFAULT 'scheduled',
    queue_open BOOLEAN NOT NULL DEFAULT FALSE,
    start_time TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    password TEXT,
    created_by_discord_id TEXT,
    created_by_name TEXT,
    announcement_message_id BIGINT,
    queue_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_queue (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    rider_id BIGINT REFERENCES riders(id) ON DELETE SET NULL,
    discord_user_id TEXT NOT NULL,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_id, discord_user_id)
);

CREATE TABLE IF NOT EXISTS event_countdowns_sent (
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    minute_mark INTEGER NOT NULL,
    PRIMARY KEY (event_id, minute_mark)
);

CREATE TABLE IF NOT EXISTS results (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    rider_id BIGINT NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    position INTEGER,
    points INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_id, rider_id)
);

CREATE TABLE IF NOT EXISTS gate_orders (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    rider_id BIGINT NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    gate_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_id, rider_id),
    UNIQUE(event_id, gate_order)
);

CREATE TABLE IF NOT EXISTS suspensions (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT,
    steam_id TEXT,
    rider_guid TEXT,
    reason TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ,
    created_by_discord_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mod_uploads (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    discord_username TEXT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    saved_path TEXT NOT NULL,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    detected_roots TEXT,
    notes TEXT,
    approved_by_discord_id TEXT,
    approved_at TIMESTAMPTZ,
    rejected_by_discord_id TEXT,
    rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_status_start_time
ON events(status, start_time);

CREATE INDEX IF NOT EXISTS idx_results_event_id
ON results(event_id);

CREATE INDEX IF NOT EXISTS idx_gate_orders_event_id
ON gate_orders(event_id);

CREATE INDEX IF NOT EXISTS idx_event_queue_event_id
ON event_queue(event_id);

CREATE INDEX IF NOT EXISTS idx_suspensions_lookup
ON suspensions(discord_user_id, steam_id, rider_guid, is_active);

CREATE INDEX IF NOT EXISTS idx_mod_uploads_status_created_at

    CREATE TABLE IF NOT EXISTS championship_series (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    discipline TEXT NOT NULL, -- MXGP or SMX
    season_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS championship_rounds (
    id BIGSERIAL PRIMARY KEY,
    series_id BIGINT NOT NULL REFERENCES championship_series(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    round_name TEXT NOT NULL,
    track_name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    scheduled_start TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled/live/finished
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS round_sessions (
    id BIGSERIAL PRIMARY KEY,
    round_id BIGINT NOT NULL REFERENCES championship_rounds(id) ON DELETE CASCADE,
    session_type TEXT NOT NULL, -- qualifier/race1/race2
    session_order INTEGER NOT NULL,
    event_id BIGINT REFERENCES events(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled/live/finished
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    UNIQUE(round_id, session_type)
);

CREATE TABLE IF NOT EXISTS session_results (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES round_sessions(id) ON DELETE CASCADE,
    rider_id BIGINT NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    position INTEGER,
    points INTEGER NOT NULL DEFAULT 0,
    gate_pick INTEGER,
    laps_completed INTEGER,
    total_time_ms BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, rider_id)
);

CREATE TABLE IF NOT EXISTS round_overall_results (
    id BIGSERIAL PRIMARY KEY,
    round_id BIGINT NOT NULL REFERENCES championship_rounds(id) ON DELETE CASCADE,
    rider_id BIGINT NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    overall_position INTEGER,
    total_points INTEGER NOT NULL DEFAULT 0,
    moto1_points INTEGER NOT NULL DEFAULT 0,
    moto2_points INTEGER NOT NULL DEFAULT 0,
    qualifier_position INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(round_id, rider_id)
);

CREATE INDEX IF NOT EXISTS idx_championship_rounds_series_id
ON championship_rounds(series_id);

CREATE INDEX IF NOT EXISTS idx_round_sessions_round_id
ON round_sessions(round_id);

CREATE INDEX IF NOT EXISTS idx_round_sessions_event_id
ON round_sessions(event_id);

CREATE INDEX IF NOT EXISTS idx_session_results_session_id
ON session_results(session_id);

CREATE INDEX IF NOT EXISTS idx_round_overall_results_round_id
ON round_overall_results(round_id);
ON mod_uploads(status, created_at);
