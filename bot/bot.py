import os
import io
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
ADMIN_ROLE_IDS = [x.strip() for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x.strip()]
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN or DISCORD_BOT_TOKEN is missing")
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

GUILD_OBJ = discord.Object(id=int(GUILD_ID))

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS riders (
                    id SERIAL PRIMARY KEY,
                    discord_id TEXT UNIQUE NOT NULL,
                    discord_name TEXT,
                    mxb_name TEXT,
                    steam_id TEXT,
                    guid TEXT,
                    guid_status TEXT DEFAULT 'pending',
                    team_name TEXT,
                    rider_number TEXT,
                    class_name TEXT,
                    approved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    track TEXT NOT NULL,
                    class_name TEXT DEFAULT 'Open',
                    season TEXT DEFAULT 'S1',
                    round_number INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'open',
                    event_type TEXT DEFAULT 'practice',
                    requires_guid BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(event_id, rider_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS qualifying_times (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    guid TEXT,
                    lap_time_ms INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            conn.commit()

def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if RACE_DIRECTOR_ROLE_ID and any(str(role.id) == str(RACE_DIRECTOR_ROLE_ID) for role in member.roles):
        return True
    if ADMIN_ROLE_IDS and any(str(role.id) in ADMIN_ROLE_IDS for role in member.roles):
        return True
    return False

def parse_lap_time_to_ms(text: str) -> int:
    text = text.strip()
    if ":" in text:
        mins, rest = text.split(":")
        secs, millis = rest.split(".")
        return int(mins) * 60000 + int(secs) * 1000 + int(millis)
    if "." in text:
        secs, millis = text.split(".")
        return int(secs) * 1000 + int(millis)
    return int(text)

def format_ms(ms: int) -> str:
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"

class LeagueBot(commands.Bot):
    async def setup_hook(self) -> None:
        init_db()

        # Force-clear old cached guild commands
        self.tree.clear_commands(guild=GUILD_OBJ)

        @self.tree.command(name="ping", description="Check if the bot is alive", guild=GUILD_OBJ)
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("🏓 Pong! Bot is working")

        @self.tree.command(name="register_mxb", description="Register your MX Bikes profile", guild=GUILD_OBJ)
        @app_commands.describe(
            mxb_name="Your MX Bikes in-game name",
            steam_id="Your Steam ID",
            guid="Your MX Bikes GUID"
        )
        async def register_mxb(
            interaction: discord.Interaction,
            mxb_name: str,
            steam_id: str,
            guid: str
        ):
            discord_id = str(interaction.user.id)
            discord_name = str(interaction.user)

            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id FROM riders WHERE guid = %s AND discord_id <> %s",
                        (guid, discord_id)
                    )
                    if cur.fetchone():
                        await interaction.response.send_message(
                            "❌ That GUID is already linked to another rider.",
                            ephemeral=True
                        )
                        return

                    cur.execute("""
                        INSERT INTO riders (
                            discord_id, discord_name, mxb_name, steam_id, guid,
                            guid_status, approved
                        )
                        VALUES (%s,%s,%s,%s,%s,'pending',FALSE)
                        ON CONFLICT (discord_id) DO UPDATE SET
                            discord_name = EXCLUDED.discord_name,
                            mxb_name = EXCLUDED.mxb_name,
                            steam_id = EXCLUDED.steam_id,
                            guid = EXCLUDED.guid,
                            guid_status = 'pending',
                            approved = FALSE
                    """, (discord_id, discord_name, mxb_name, steam_id, guid))
                    conn.commit()

            try:
                await interaction.user.edit(nick=mxb_name)
            except Exception:
                pass

            await interaction.response.send_message(
                f"✅ Registered as **{mxb_name}**. GUID is now pending approval.",
                ephemeral=True
            )

        @self.tree.command(name="guid_status", description="Check your GUID approval status", guild=GUILD_OBJ)
        async def guid_status(interaction: discord.Interaction):
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT mxb_name, steam_id, guid, guid_status, class_name, approved
                        FROM riders
                        WHERE discord_id = %s
                    """, (str(interaction.user.id),))
                    rider = cur.fetchone()

            if not rider:
                await interaction.response.send_message(
                    "❌ You are not registered yet. Use /register_mxb first.",
                    ephemeral=True
                )
                return

            msg = (
                f"**MXB Name:** {rider['mxb_name'] or 'Not set'}\n"
                f"**Steam ID:** {rider['steam_id'] or 'Not set'}\n"
                f"**GUID:** {rider['guid'] or 'Not set'}\n"
                f"**GUID Status:** {rider['guid_status']}\n"
                f"**Class:** {rider['class_name'] or 'Not set'}\n"
                f"**Approved:** {rider['approved']}"
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @self.tree.command(name="set_guid_status", description="Approve or reject a rider GUID", guild=GUILD_OBJ)
        @app_commands.describe(member="Discord member", status="approved, mismatch, rejected, pending")
        async def set_guid_status(interaction: discord.Interaction, member: discord.Member, status: str):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            allowed = {"approved", "mismatch", "rejected", "pending"}
            if status not in allowed:
                await interaction.response.send_message(
                    "❌ Status must be approved, mismatch, rejected, or pending.",
                    ephemeral=True
                )
                return

            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE riders
                        SET guid_status = %s,
                            approved = CASE WHEN %s = 'approved' THEN TRUE ELSE FALSE END
                        WHERE discord_id = %s
                    """, (status, status, str(member.id)))
                    conn.commit()

            await interaction.response.send_message(
                f"✅ {member.mention} GUID status set to **{status}**."
            )

        @self.tree.command(name="create_event", description="Create a race event", guild=GUILD_OBJ)
        @app_commands.describe(
            name="Event name",
            track="Track name"
        )
        async def create_event(
            interaction: discord.Interaction,
            name: str,
            track: str
        ):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO events (name, track, season, status, event_type, requires_guid)
                        VALUES (%s,%s,%s,'open','practice',FALSE)
                        RETURNING id
                    """, (name, track, DEFAULT_SEASON))
                    event_id = cur.fetchone()[0]
                    conn.commit()

            if RACE_ANNOUNCEMENTS_CHANNEL_ID:
                channel = self.get_channel(int(RACE_ANNOUNCEMENTS_CHANNEL_ID))
                if channel:
                    await channel.send(
                        f"🏁 **New Event Created**\n"
                        f"**ID:** {event_id}\n"
                        f"**Name:** {name}\n"
                        f"**Track:** {track}"
                    )

            await interaction.response.send_message(f"✅ Event created with ID **{event_id}**.")

        @self.tree.command(name="list_events", description="List current events", guild=GUILD_OBJ)
        async def list_events(interaction: discord.Interaction):
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT id, name, track, event_type, status FROM events ORDER BY id DESC LIMIT 10")
                    events = cur.fetchall()

            if not events:
                await interaction.response.send_message("No events found.", ephemeral=True)
                return

            lines = [
                f"**#{e['id']}** — {e['name']} | {e['track']} | {e['event_type']} | {e['status']}"
                for e in events
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        synced = await self.tree.sync(guild=GUILD_OBJ)
        print(f"Synced {len(synced)} guild command(s) to guild {GUILD_ID}")

bot = LeagueBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user} ({bot.user.id})")

bot.run(TOKEN)
