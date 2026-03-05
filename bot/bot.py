import os
import asyncio
import psycopg2
import psycopg2.extras

import discord
from discord import app_commands

# ----------------------------
# Env
# ----------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", os.getenv("DISCORD_TOKEN", ""))

DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID", os.getenv("RACE_ANNOUNCEMENTS_CHAN", ""))
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID", "")

def must(env_name: str, v: str):
    if not v:
        raise RuntimeError(f"Missing {env_name} env var")

must("DATABASE_URL", DATABASE_URL)
must("DISCORD_BOT_TOKEN", DISCORD_BOT_TOKEN)
must("DISCORD_GUILD_ID", DISCORD_GUILD_ID)

GUILD_ID_INT = int(DISCORD_GUILD_ID)

# ----------------------------
# DB helpers
# ----------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def db_exec(sql, params=None, fetch="none"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or {})
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None

def ensure_schema():
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            steam_id TEXT,
            mxb_name TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            track TEXT,
            bike_class TEXT,
            start_time TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            discord_id TEXT,
            rider_name TEXT,
            position INTEGER,
            points INTEGER,
            raw_html TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS points INTEGER;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS rider_name TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS discord_id TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS raw_html TEXT;""")

def is_race_director(member: discord.Member) -> bool:
    # If you didn't set RACE_DIRECTOR_ROLE_ID, allow admins only
    if RACE_DIRECTOR_ROLE_ID:
        rid = int(RACE_DIRECTOR_ROLE_ID)
        return any(r.id == rid for r in member.roles)
    return member.guild_permissions.administrator

# ----------------------------
# Discord bot setup
# ----------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = False  # you don't need privileged intents for slash commands

class LeagueBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

bot = LeagueBot()

# ----------------------------
# Commands
# ----------------------------
@bot.tree.command(name="ping", description="Check if the bot is alive")
@app_commands.guilds(discord.Object(id=GUILD_ID_INT))
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! Bot is online.", ephemeral=True)

@bot.tree.command(name="standings", description="Show top 10 standings by points")
@app_commands.guilds(discord.Object(id=GUILD_ID_INT))
async def standings(interaction: discord.Interaction):
    rows = db_exec(
        """
        SELECT
          COALESCE(u.mxb_name, u.discord_name, r.rider_name, r.discord_id) AS rider,
          SUM(COALESCE(r.points, 0))::int AS points
        FROM results r
        LEFT JOIN users u ON u.id = r.user_id OR u.discord_id = r.discord_id
        GROUP BY rider
        ORDER BY points DESC, rider ASC
        LIMIT 10;
        """,
        fetch="all"
    ) or []

    if not rows:
        await interaction.response.send_message("No standings yet (no results uploaded).", ephemeral=True)
        return

    lines = [f"**{i+1}.** {r['rider']} — **{r['points']} pts**" for i, r in enumerate(rows)]
    await interaction.response.send_message("🏁 **Standings (Top 10)**\n" + "\n".join(lines), ephemeral=False)

@bot.tree.command(name="event_create", description="Create a new active event (Race Director only)")
@app_commands.guilds(discord.Object(id=GUILD_ID_INT))
@app_commands.describe(name="Event name", track="Track name", bike_class="Bike class (e.g. 450/250/2T)")
async def event_create(interaction: discord.Interaction, name: str, track: str = "", bike_class: str = ""):
    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.response.send_message("❌ You don’t have permission (Race Director/Admin only).", ephemeral=True)
        return

    # close old events
    db_exec("UPDATE events SET is_active=FALSE WHERE is_active=TRUE;")
    db_exec(
        "INSERT INTO events (name, track, bike_class, is_active) VALUES (%(n)s, %(t)s, %(c)s, TRUE);",
        {"n": name.strip(), "t": track.strip(), "c": bike_class.strip()},
    )

    await interaction.response.send_message(f"✅ Event created and set active: **{name}**", ephemeral=False)

    # Optional announce
    if RACE_ANNOUNCEMENTS_CHANNEL_ID:
        ch = interaction.guild.get_channel(int(RACE_ANNOUNCEMENTS_CHANNEL_ID))
        if ch:
            await ch.send(f"📣 **New Event Live!**\n**{name}**\nTrack: {track or 'TBA'} | Class: {bike_class or 'Open'}")

@bot.tree.command(name="event_close", description="Close the current active event (Race Director only)")
@app_commands.guilds(discord.Object(id=GUILD_ID_INT))
async def event_close(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.response.send_message("❌ You don’t have permission (Race Director/Admin only).", ephemeral=True)
        return

    db_exec("UPDATE events SET is_active=FALSE WHERE is_active=TRUE;")
    await interaction.response.send_message("✅ Active event closed.", ephemeral=False)

# ----------------------------
# Ready / Sync
# ----------------------------
@bot.event
async def on_ready():
    # Ensure DB is ready
    ensure_schema()

    # Sync slash commands to your guild (fast + avoids global propagation delays)
    guild_obj = discord.Object(id=GUILD_ID_INT)

    # Backoff to reduce chance of rate-limit if Render restarts a lot
    for attempt in range(1, 6):
        try:
            await bot.tree.sync(guild=guild_obj)
            break
        except Exception:
            await asyncio.sleep(3 * attempt)

    print(f"✅ Bot logged in as {bot.user} (guild {GUILD_ID_INT})")

# ----------------------------
# Run
# ----------------------------
bot.run(DISCORD_BOT_TOKEN)
