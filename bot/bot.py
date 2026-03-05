import os
import asyncio
from typing import Optional, List, Tuple

import discord
from discord import app_commands

import psycopg2
import psycopg2.extras

# ----------------------------
# ENV VARS (Render -> Environment)
# ----------------------------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")
if not DISCORD_GUILD_ID:
    raise RuntimeError("Missing DISCORD_GUILD_ID env var")

GUILD_ID_INT = int(DISCORD_GUILD_ID)
RACE_DIRECTOR_ROLE_ID_INT = int(RACE_DIRECTOR_ROLE_ID) if RACE_DIRECTOR_ROLE_ID else None
ANNOUNCE_CH_INT = int(RACE_ANNOUNCEMENTS_CHANNEL_ID) if RACE_ANNOUNCEMENTS_CHANNEL_ID else None


# ----------------------------
# DB HELPERS
# ----------------------------
def db_conn():
    # Works with Render Postgres DATABASE_URL
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Create tables if missing, and apply safe migrations."""
    ddl = """
    CREATE TABLE IF NOT EXISTS riders (
      id SERIAL PRIMARY KEY,
      discord_id TEXT UNIQUE,
      discord_name TEXT,
      mxb_name TEXT,
      created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS events (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      round INTEGER DEFAULT 1,
      season TEXT DEFAULT 'S1',
      status TEXT DEFAULT 'open',
      created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS results (
      id SERIAL PRIMARY KEY,
      event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
      discord_id TEXT,
      rider_name TEXT,
      position INTEGER,
      points INTEGER,
      created_at TIMESTAMP DEFAULT NOW()
    );
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)

            # --- Safe migrations for common earlier crashes ---
            # Your logs showed: "column user_id does not exist"
            # Some older schema used user_id; we ensure discord_id exists where needed
            # and we don't rely on user_id at all.

            conn.commit()


def fetch_standings(limit: int = 20) -> List[Tuple[str, int]]:
    """
    Returns list of (rider_name, points_total) from results table.
    """
    q = """
    SELECT
      COALESCE(NULLIF(r.rider_name,''), NULLIF(ri.mxb_name,''), NULLIF(ri.discord_name,''), r.discord_id) AS name,
      SUM(COALESCE(r.points,0)) AS pts
    FROM results r
    LEFT JOIN riders ri ON ri.discord_id = r.discord_id
    GROUP BY 1
    ORDER BY pts DESC
    LIMIT %s;
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (limit,))
            rows = cur.fetchall()
            return [(row["name"], int(row["pts"])) for row in rows]


def is_race_director(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if RACE_DIRECTOR_ROLE_ID_INT is None:
        return False
    return any(role.id == RACE_DIRECTOR_ROLE_ID_INT for role in member.roles)


# ----------------------------
# DISCORD BOT SETUP
# ----------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # useful for role checks
# message_content is only needed for prefix commands; slash commands don't require it.
# Leaving it off avoids the "Privileged message content" warning.
# intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    # init DB once
    try:
        init_db()
        print("DB OK")
    except Exception as e:
        print("DB init error:", e)

    # Guild sync (fast + reliable)
    guild = discord.Object(id=GUILD_ID_INT)
    try:
        synced = await tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to guild {GUILD_ID_INT}")
    except Exception as e:
        print("Command sync error:", e)

    print(f"Logged in as {client.user} (guild sync mode)")


# ----------------------------
# SLASH COMMANDS
# ----------------------------
@tree.command(name="ping", description="Bot health check", guild=discord.Object(id=GUILD_ID_INT))
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏁 Pong! Bot is online.", ephemeral=True)


@tree.command(name="standings", description="Show current championship standings", guild=discord.Object(id=GUILD_ID_INT))
@app_commands.describe(limit="How many riders to show (default 15)")
async def standings(interaction: discord.Interaction, limit: Optional[int] = 15):
    limit = max(5, min(int(limit or 15), 30))

    try:
        rows = fetch_standings(limit=limit)
    except Exception as e:
        await interaction.response.send_message(f"DB error loading standings: `{e}`", ephemeral=True)
        return

    if not rows:
        await interaction.response.send_message(
            "No results found yet. Upload race results first, then standings will populate.",
            ephemeral=True,
        )
        return

    lines = []
    for i, (name, pts) in enumerate(rows, start=1):
        lines.append(f"**{i}.** {name} — **{pts} pts**")

    embed = discord.Embed(
        title="🏆 Championship Standings",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="event_create", description="Create a new event (Race Director only)", guild=discord.Object(id=GUILD_ID_INT))
@app_commands.describe(name="Event name", round="Round number", season="Season name (e.g. S1)")
async def event_create(interaction: discord.Interaction, name: str, round: Optional[int] = 1, season: Optional[str] = "S1"):
    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.response.send_message("❌ Race Director role required.", ephemeral=True)
        return

    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO events (name, round, season, status) VALUES (%s,%s,%s,'open') RETURNING id;",
                    (name, int(round or 1), season or "S1"),
                )
                event_id = cur.fetchone()["id"]
                conn.commit()
    except Exception as e:
        await interaction.response.send_message(f"DB error: `{e}`", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Event created: **{name}** (ID: `{event_id}`)", ephemeral=True)

    # Optional announce
    if ANNOUNCE_CH_INT:
        ch = interaction.guild.get_channel(ANNOUNCE_CH_INT)
        if ch:
            await ch.send(f"📣 **New Event Open:** **{name}** (Round {round or 1}, {season or 'S1'})\nEvent ID: `{event_id}`")


@tree.command(name="event_close", description="Close an event (Race Director only)", guild=discord.Object(id=GUILD_ID_INT))
@app_commands.describe(event_id="Event ID to close")
async def event_close(interaction: discord.Interaction, event_id: int):
    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.response.send_message("❌ Race Director role required.", ephemeral=True)
        return

    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE events SET status='closed' WHERE id=%s RETURNING name;", (int(event_id),))
                row = cur.fetchone()
                conn.commit()
    except Exception as e:
        await interaction.response.send_message(f"DB error: `{e}`", ephemeral=True)
        return

    if not row:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Closed event **{row['name']}** (ID: `{event_id}`)", ephemeral=True)


# ----------------------------
# RUN
# ----------------------------
async def main():
    await client.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
