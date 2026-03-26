import os
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")

RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
ADMIN_ROLE_IDS = [x.strip() for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x.strip()]
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN or DISCORD_BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID is missing")

GUILD_ID_INT = int(GUILD_ID)
GUILD_OBJ = discord.Object(id=GUILD_ID_INT)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True


def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with get_db() as conn:
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
            conn.commit()


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if RACE_DIRECTOR_ROLE_ID and any(str(r.id) == str(RACE_DIRECTOR_ROLE_ID) for r in member.roles):
        return True
    if ADMIN_ROLE_IDS and any(str(r.id) in ADMIN_ROLE_IDS for r in member.roles):
        return True
    return False


class LeagueBot(commands.Bot):
    async def setup_hook(self) -> None:
        init_db()

        # Clear old cached guild commands
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

            with get_db() as conn:
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
                if interaction.guild:
                    me_member = interaction.guild.me
                    if me_member and me_member.guild_permissions.manage_nicknames:
                        await interaction.user.edit(nick=mxb_name)
            except Exception:
                pass

            await interaction.response.send_message(
                f"✅ Registered as **{mxb_name}**.\nGUID is pending approval.",
                ephemeral=True
            )

        @self.tree.command(name="link_steam", description="Update only your Steam ID", guild=GUILD_OBJ)
        @app_commands.describe(steam_id="Your Steam ID")
        async def link_steam(interaction: discord.Interaction, steam_id: str):
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO riders (discord_id, discord_name, steam_id)
                        VALUES (%s,%s,%s)
                        ON CONFLICT (discord_id) DO UPDATE SET
                            discord_name = EXCLUDED.discord_name,
                            steam_id = EXCLUDED.steam_id
                    """, (str(interaction.user.id), str(interaction.user), steam_id))
                    conn.commit()

            await interaction.response.send_message(
                f"✅ Steam ID linked: **{steam_id}**",
                ephemeral=True
            )

        @self.tree.command(name="guid_status", description="Check your GUID approval status", guild=GUILD_OBJ)
        async def guid_status(interaction: discord.Interaction):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT mxb_name, steam_id, guid, guid_status, approved
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

            await interaction.response.send_message(
                f"**MXB Name:** {rider['mxb_name'] or 'Not set'}\n"
                f"**Steam ID:** {rider['steam_id'] or 'Not set'}\n"
                f"**GUID:** {rider['guid'] or 'Not set'}\n"
                f"**GUID Status:** {rider['guid_status']}\n"
                f"**Approved:** {rider['approved']}",
                ephemeral=True
            )

        @self.tree.command(name="my_profile", description="Show your saved profile", guild=GUILD_OBJ)
        async def my_profile(interaction: discord.Interaction):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT mxb_name, steam_id, guid, guid_status, team_name, rider_number, class_name
                        FROM riders
                        WHERE discord_id = %s
                    """, (str(interaction.user.id),))
                    rider = cur.fetchone()

            if not rider:
                await interaction.response.send_message(
                    "❌ No profile found yet. Use /register_mxb first.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"**MXB Name:** {rider['mxb_name'] or '-'}\n"
                f"**Steam ID:** {rider['steam_id'] or '-'}\n"
                f"**GUID:** {rider['guid'] or '-'}\n"
                f"**GUID Status:** {rider['guid_status'] or '-'}\n"
                f"**Team:** {rider['team_name'] or '-'}\n"
                f"**Rider Number:** {rider['rider_number'] or '-'}\n"
                f"**Class:** {rider['class_name'] or '-'}",
                ephemeral=True
            )

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

            with get_db() as conn:
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
            track="Track name",
            event_type="practice, qualifier, finals"
        )
        async def create_event(
            interaction: discord.Interaction,
            name: str,
            track: str,
            event_type: str = "practice"
        ):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            event_type = event_type.lower().strip()
            if event_type not in {"practice", "qualifier", "finals"}:
                await interaction.response.send_message(
                    "❌ event_type must be practice, qualifier, or finals.",
                    ephemeral=True
                )
                return

            requires_guid = event_type in {"qualifier", "finals"}

            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO events (name, track, season, status, event_type, requires_guid)
                        VALUES (%s,%s,%s,'open',%s,%s)
                        RETURNING id
                    """, (name, track, DEFAULT_SEASON, event_type, requires_guid))
                    event_id = cur.fetchone()[0]
                    conn.commit()

            if RACE_ANNOUNCEMENTS_CHANNEL_ID:
                channel = self.get_channel(int(RACE_ANNOUNCEMENTS_CHANNEL_ID))
                if channel:
                    await channel.send(
                        f"🏁 **New Event Created**\n"
                        f"**ID:** {event_id}\n"
                        f"**Name:** {name}\n"
                        f"**Track:** {track}\n"
                        f"**Type:** {event_type}\n"
                        f"**GUID Lock:** {'Yes' if requires_guid else 'No'}"
                    )

            await interaction.response.send_message(f"✅ Event created with ID **{event_id}**.")

        @self.tree.command(name="list_events", description="List current events", guild=GUILD_OBJ)
        async def list_events(interaction: discord.Interaction):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, name, track, event_type, status, requires_guid
                        FROM events
                        ORDER BY id DESC
                        LIMIT 10
                    """)
                    events = cur.fetchall()

            if not events:
                await interaction.response.send_message("No events found.", ephemeral=True)
                return

            lines = []
            for e in events:
                lines.append(
                    f"**#{e['id']}** — {e['name']} | {e['track']} | {e['event_type']} | {e['status']} | GUID: {'Yes' if e['requires_guid'] else 'No'}"
                )

            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @self.tree.command(name="join_race", description="Join an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def join_race(interaction: discord.Interaction, event_id: int):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(interaction.user.id),))
                    rider = cur.fetchone()
                    if not rider:
                        await interaction.response.send_message(
                            "❌ You must register first with /register_mxb.",
                            ephemeral=True
                        )
                        return

                    cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                    event = cur.fetchone()
                    if not event:
                        await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                        return

                    if event["requires_guid"] and rider["guid_status"] != "approved":
                        await interaction.response.send_message(
                            "❌ This event is GUID locked. Your GUID is not approved yet.",
                            ephemeral=True
                        )
                        return

                    cur.execute("""
                        INSERT INTO registrations (event_id, rider_id)
                        VALUES (%s,%s)
                        ON CONFLICT (event_id, rider_id) DO NOTHING
                    """, (event_id, rider["id"]))
                    conn.commit()

            await interaction.response.send_message(f"✅ Joined event **#{event_id}**.", ephemeral=True)

        try:
            synced = await self.tree.sync(guild=GUILD_OBJ)
            print(f"Synced {len(synced)} guild command(s) to guild {GUILD_ID}")
        except Exception as e:
            print(f"Sync failed: {e}")


bot = LeagueBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user} ({bot.user.id})")

bot.run(TOKEN)
