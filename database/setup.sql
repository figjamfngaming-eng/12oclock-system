CREATE TABLE IF NOT EXISTS site_users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    discord_user_id TEXT UNIQUE,
    discord_username TEXT,
    discord_avatar TEXT,
    discord_email TEXT,
    verified_discord BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_links (
    id SERIAL PRIMARY KEY,
    site_user_id INTEGER NOT NULL REFERENCES site_users(id) ON DELETE CASCADE,
    discord_id TEXT,
    discord_username TEXT,
    steam_id TEXT,
    steam_name TEXT,
    link_status TEXT DEFAULT 'pending',
    approved BOOLEAN DEFAULT FALSE,
    auto_approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_user_id)
);

CREATE TABLE IF NOT EXISTS riders (
    id SERIAL PRIMARY KEY,
    discord_id TEXT UNIQUE,
    discord_user_id TEXT,
    discord_username TEXT,
    mxb_name TEXT,
    guid TEXT,
    steam_id TEXT,
    class_name TEXT,
    is_linked BOOLEAN DEFAULT FALSE,
    approved BOOLEAN DEFAULT FALSE,
    auto_approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    series TEXT DEFAULT 'MXGP',
    class_name TEXT NOT NULL,
    race_stage TEXT DEFAULT 'qualifying',
    race_password TEXT,
    status TEXT DEFAULT 'pending',
    queue_open BOOLEAN DEFAULT FALSE,
    track_name TEXT,
    winner_rider_id INTEGER,
    winner_name TEXT,
    finished_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS results (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(event_id, rider_id)
);

CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT DEFAULT 'general',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_state (
    id SERIAL PRIMARY KEY,
    is_live BOOLEAN DEFAULT FALSE,
    event_id INTEGER,
    series TEXT,
    class_name TEXT,
    track_name TEXT,
    server_name TEXT,
    queue_open BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS winner_feed (
    id SERIAL PRIMARY KEY,
    event_id INTEGER,
    rider_name TEXT NOT NULL,
    series TEXT,
    class_name TEXT,
    position INTEGER DEFAULT 1,
    points INTEGER DEFAULT 0,
    feed_type TEXT DEFAULT 'race_winner',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_results (
    id SERIAL PRIMARY KEY,
    event_id INTEGER,
    rider_name TEXT NOT NULL,
    position INTEGER,
    laps INTEGER DEFAULT 0,
    best_lap TEXT,
    gap TEXT,
    status TEXT DEFAULT 'running',
    updated_at TIMESTAMP DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS gate_orders (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
    gate_order INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(event_id, rider_id),
    UNIQUE(event_id, gate_order)
);
