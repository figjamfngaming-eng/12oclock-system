import os
import asyncio
from datetime import datetime

import discord
from discord import app_commands
import psycopg2
import psycopg2.extras

# ---------------------------
# ENV
# ---------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Optional restrictions
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")  # set for faster slash sync
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0") or "0")
ROLE_RACE_DIRECTOR_ID = int(os.getenv("ROLE_RACE_DIRECTOR_ID", "0") or "0")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

# ---------------------------
# DB
# ---------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_user_id BIGINT UNIQUE,
        discord_tag TEXT,
        steam_id TEXT,
        mxb_name TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        mode TEXT NOT NULL,
        class_name TEXT NOT NULL,
        title TEXT NOT NULL,
        season INT NOT NULL DEFAULT 1,
        track TEXT,
        notes TEXT,
        start_time TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS race_results (
        id SERIAL PRIMARY KEY,
        event_id INT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        source TEXT DEFAULT 'upload',
        uploaded_at TIMESTAMPTZ DEFAULT NOW(),
        raw_html TEXT,
        parsed_json JSONB
    );

    CREATE TABLE IF NOT EXISTS standings_points (
        id SERIAL PRIMARY KEY,
        event_id INT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        class_name TEXT NOT NULL,
        rider_name TEXT NOT NULL,
        points INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

init_db()

# ---------------------------
# Discord
# ---------------------------
intents = discord.Intents.default()

class LeagueBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Sync commands to a single guild for fast updates (recommended)
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

client = LeagueBot()

def is_race_director(interaction: discord.Interaction) -> bool:
    if ROLE_RACE_DIRECTOR_ID == 0:
        # if you didn't set a role, allow admins
        return interaction.user.guild_permissions.administrator
    return any(getattr(r, "id", None) == ROLE_RACE_DIRECTOR_ID for r in getattr(interaction.user, "roles", []))

# ---------------------------
# Commands
# ---------------------------
@client.tree.command(name="ping", description="Check if the league bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! AMA League bot online.", ephemeral=True)

@client.tree.command(name="create_event", description="Create a new event (Race Director only)")
@app_commands.describe(
    mode="MX / SX / ENDURO",
    class_name="450 / 250 / etc",
    title="Event title",
    season="Season number",
    track="Track name",
    start="Start time text (optional)",
    notes="Notes (optional)"
)
async def create_event(
    interaction: discord.Interaction,
    mode: str,
    class_name: str,
    title: str,
    season: int = 1,
    track: str = "",
    start: str = "",
    notes: str = "",
):
    if not is_race_director(interaction):
        await interaction.response.send_message("❌ You must be Race Director/Admin to use this.", ephemeral=True)
        return

    start_dt = None
    if start.strip():
        # store as text-ish; you can standardize later
        try:
            start_dt = datetime.fromisoformat(start.strip())
        except Exception:
            start_dt = None

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO events (mode, class_name, title, season, track, notes, start_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING *;
                """,
                (mode.strip().upper(), class_name.strip(), title.strip(), int(season), track.strip(), notes.strip(), start_dt),
            )
            ev = cur.fetchone()
        conn.commit()

    msg = f"✅ Event created: **#{ev['id']}** — {ev['mode']} {ev['class_name']} — **{ev['title']}**"
    await interaction.response.send_message(msg, ephemeral=True)

@client.tree.command(name="announce_event", description="Post an event card in race announcements (Race Director only)")
@app_commands.describe(event_id="Event ID to announce")
async def announce_event(interaction: discord.Interaction, event_id: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("❌ You must be Race Director/Admin to use this.", ephemeral=True)
        return

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM events WHERE id=%s;", (int(event_id),))
            ev = cur.fetchone()
        conn.commit()

    if not ev:
        await interaction.response.send_message("❌ Event not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🏁 {ev['mode']} {ev['class_name']} — {ev['title']}",
        description=(ev["notes"] or "").strip() or "Race event created.",
        color=0x7C3AED,
    )
    if ev.get("track"):
        embed.add_field(name="Track", value=ev["track"], inline=True)
    embed.add_field(name="Season", value=str(ev["season"]), inline=True)
    if ev.get("start_time"):
        embed.add_field(name="Start", value=str(ev["start_time"]), inline=False)

    embed.set_footer(text=f"Event ID: {ev['id']}")

    # Post to selected channel, else current channel
    channel = None
    if ANNOUNCE_CHANNEL_ID:
        channel = interaction.guild.get_channel(ANNOUNCE_CHANNEL_ID)
    if channel is None:
        channel = interaction.channel

    await channel.send(embed=embed)
    await interaction.response.send_message("✅ Announced.", ephemeral=True)

# ---------------------------
# Run
# ---------------------------
client.run(DISCORD_TOKEN)
