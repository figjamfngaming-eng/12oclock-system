import os
import psycopg2
from psycopg2.extras import RealDictCursor

def get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg2.connect(url, sslmode=os.getenv("PGSSLMODE", "prefer"))

def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        discord_id BIGINT UNIQUE,
        discord_username TEXT,
        steam_id TEXT UNIQUE,
        steam_persona TEXT,
        mxb_name TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS events (
        id BIGSERIAL PRIMARY KEY,
        season INT NOT NULL DEFAULT 1,
        mode TEXT NOT NULL CHECK (mode IN ('MX','SX','ENDURO')),
        class TEXT NOT NULL,
        title TEXT NOT NULL,
        track TEXT NOT NULL,
        start_time TIMESTAMPTZ NOT NULL,
        notes TEXT,
        created_by_discord_id BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS results (
        id BIGSERIAL PRIMARY KEY,
        event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        position INT NOT NULL,
        raw_name TEXT NOT NULL,
        user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        points INT NOT NULL DEFAULT 0,
        time_text TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(event_id, position)
    );

    CREATE TABLE IF NOT EXISTS penalties (
        id BIGSERIAL PRIMARY KEY,
        event_id BIGINT REFERENCES events(id) ON DELETE CASCADE,
        user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
        points_delta INT NOT NULL,
        reason TEXT NOT NULL,
        issued_by_discord_id BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_results_event ON results(event_id);
    CREATE INDEX IF NOT EXISTS idx_results_user ON results(user_id);
    CREATE INDEX IF NOT EXISTS idx_penalties_user ON penalties(user_id);

    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

def q(sql, args=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args or [])
            rows = cur.fetchall()
        return rows

def exec1(sql, args=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args or [])
            row = cur.fetchone()
        conn.commit()
        return row
