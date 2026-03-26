import os
import io
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
ADMIN_ROLE_IDS = [x.strip() for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x.strip()]
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is missing")
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gate_picks (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    guid TEXT,
                    gate_number INTEGER NOT NULL,
                    pick_order INTEGER NOT NULL,
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

        self.tree.clear_commands(guild=GUILD_OBJ)

        @self.tree.command(name="ping", description="Check if the bot is alive", guild=GUILD_OBJ)
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("🏓 Pong! Bot is working")

        @self.tree.command(name="register_mxb", description="Register your MX Bikes profile", guild=GUILD_OBJ)
        @app_commands.describe(
            mxb_name="Your MX Bikes in-game name",
            steam_id="Your Steam ID",
            guid="Your MX Bikes GUID",
            class_name="450, 250, or Open",
            team_name="Your team name",
            rider_number="Your rider number"
        )
        async def register_mxb(
            interaction: discord.Interaction,
            mxb_name: str,
            steam_id: str,
            guid: str,
            class_name: str = "450",
            team_name: str = "",
            rider_number: str = ""
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

                    cur.execute(
                        "SELECT id FROM riders WHERE steam_id = %s AND discord_id <> %s",
                        (steam_id, discord_id)
                    )
                    if cur.fetchone():
                        await interaction.response.send_message(
                            "❌ That Steam ID is already linked to another rider.",
                            ephemeral=True
                        )
                        return

                    cur.execute("""
                        INSERT INTO riders (
                            discord_id, discord_name, mxb_name, steam_id, guid,
                            guid_status, team_name, rider_number, class_name, approved
                        )
                        VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,%s,FALSE)
                        ON CONFLICT (discord_id) DO UPDATE SET
                            discord_name = EXCLUDED.discord_name,
                            mxb_name = EXCLUDED.mxb_name,
                            steam_id = EXCLUDED.steam_id,
                            guid = EXCLUDED.guid,
                            guid_status = 'pending',
                            team_name = EXCLUDED.team_name,
                            rider_number = EXCLUDED.rider_number,
                            class_name = EXCLUDED.class_name,
                            approved = FALSE
                    """, (
                        discord_id, discord_name, mxb_name, steam_id, guid,
                        team_name, rider_number, class_name
                    ))
                    conn.commit()

            try:
                await interaction.user.edit(nick=mxb_name)
            except Exception:
                pass

            await interaction.response.send_message(
                f"✅ Profile saved for **{mxb_name}**.\nYour GUID is now pending approval.",
                ephemeral=True
            )

        @self.tree.command(name="guid_status", description="Check your GUID approval status", guild=GUILD_OBJ)
        async def guid_status(interaction: discord.Interaction):
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT mxb_name, steam_id, guid, guid_status, class_name, approved FROM riders WHERE discord_id = %s",
                        (str(interaction.user.id),)
                    )
                    rider = cur.fetchone()

            if not rider:
                await interaction.response.send_message(
                    "❌ You have not registered yet. Use /register_mxb first.",
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
            track="Track name",
            class_name="450, 250, or Open",
            event_type="practice, qualifier, or finals",
            round_number="Round number"
        )
        async def create_event(
            interaction: discord.Interaction,
            name: str,
            track: str,
            class_name: str = "Open",
            event_type: str = "practice",
            round_number: int = 1
        ):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            requires_guid = event_type in ("qualifier", "finals")

            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO events (name, track, class_name, season, round_number, status, event_type, requires_guid)
                        VALUES (%s,%s,%s,%s,%s,'open',%s,%s)
                        RETURNING id
                    """, (name, track, class_name, DEFAULT_SEASON, round_number, event_type, requires_guid))
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
                        f"**Class:** {class_name}\n"
                        f"**Type:** {event_type}\n"
                        f"**GUID Lock:** {'Yes' if requires_guid else 'No'}"
                    )

            await interaction.response.send_message(f"✅ Event created with ID **{event_id}**.")

        @self.tree.command(name="list_events", description="List current events", guild=GUILD_OBJ)
        async def list_events(interaction: discord.Interaction):
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM events ORDER BY id DESC LIMIT 10")
                    events = cur.fetchall()

            if not events:
                await interaction.response.send_message("No events found.", ephemeral=True)
                return

            lines = []
            for e in events:
                lines.append(
                    f"**#{e['id']}** — {e['name']} | {e['track']} | {e['class_name']} | {e['event_type']} | {e['status']}"
                )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @self.tree.command(name="join_race", description="Join an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def join_race(interaction: discord.Interaction, event_id: int):
            with db() as conn:
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
                            "❌ GUID mismatch: You are not approved for this event. Update your GUID and get it approved.",
                            ephemeral=True
                        )
                        return

                    cur.execute(
                        "SELECT id FROM registrations WHERE event_id = %s AND rider_id = %s",
                        (event_id, rider["id"])
                    )
                    if cur.fetchone():
                        await interaction.response.send_message(
                            "✅ You are already registered for this event.",
                            ephemeral=True
                        )
                        return

                    cur.execute(
                        "INSERT INTO registrations (event_id, rider_id) VALUES (%s,%s)",
                        (event_id, rider["id"])
                    )
                    conn.commit()

            await interaction.response.send_message(f"✅ You joined event **#{event_id}**.")

        @self.tree.command(name="set_qualy_time", description="Set a rider qualifying time", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID", member="Rider", lap_time="Example 1:02.341")
        async def set_qualy_time(interaction: discord.Interaction, event_id: int, member: discord.Member, lap_time: str):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            lap_time_ms = parse_lap_time_to_ms(lap_time)

            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(member.id),))
                    rider = cur.fetchone()
                    if not rider:
                        await interaction.response.send_message("❌ Rider is not registered.", ephemeral=True)
                        return

                    cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                    event = cur.fetchone()
                    if not event:
                        await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                        return

                    cur.execute("""
                        INSERT INTO qualifying_times (event_id, rider_id, guid, lap_time_ms)
                        VALUES (%s,%s,%s,%s)
                    """, (event_id, rider["id"], rider["guid"], lap_time_ms))
                    conn.commit()

            await interaction.response.send_message(
                f"✅ {member.mention} qualifying time set to **{lap_time}**."
            )

        @self.tree.command(name="gate_order", description="Show gate pick order from fastest laps", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def gate_order(interaction: discord.Interaction, event_id: int):
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT r.mxb_name, r.guid, MIN(q.lap_time_ms) AS best_lap
                        FROM qualifying_times q
                        JOIN riders r ON r.id = q.rider_id
                        WHERE q.event_id = %s
                        GROUP BY r.mxb_name, r.guid
                        ORDER BY best_lap ASC, r.mxb_name ASC
                    """, (event_id,))
                    rows = cur.fetchall()

            if not rows:
                await interaction.response.send_message("❌ No qualifying times found.", ephemeral=True)
                return

            lines = []
            for i, row in enumerate(rows, start=1):
                lines.append(f"{i}. {row['mxb_name']} — {format_ms(row['best_lap'])}")

            await interaction.response.send_message("🏁 **Gate Pick Order**\n" + "\n".join(lines))

        @self.tree.command(name="export_whitelist", description="Export GUID whitelist for an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def export_whitelist(interaction: discord.Interaction, event_id: int):
            if not is_staff(interaction.user):
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
                return

            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT r.mxb_name, r.guid, r.rider_number
                        FROM registrations reg
                        JOIN riders r ON r.id = reg.rider_id
                        WHERE reg.event_id = %s
                          AND r.guid_status = 'approved'
                    """, (event_id,))
                    riders = cur.fetchall()

            if not riders:
                await interaction.response.send_message(
                    "❌ No approved riders registered for this event.",
                    ephemeral=True
                )
                return

            out = io.StringIO()
            for i, rider in enumerate(riders):
                out.write(f"[entry{i}]\n")
                out.write(f"guid = {rider['guid']}\n")
                out.write(f"name = {rider['mxb_name'] or 'Rider'}\n")
                if rider["rider_number"]:
                    out.write(f"race_number = {rider['rider_number']}\n")
                out.write("name_prefix = 12OCB\n")
                out.write("spectator = 0\n")
                out.write("\n")

            file_data = out.getvalue().encode("utf-8")
            discord_file = discord.File(
                fp=io.BytesIO(file_data),
                filename=f"whitelist_event_{event_id}.txt"
            )
            await interaction.response.send_message(
                f"✅ Whitelist exported for event **#{event_id}**.",
                file=discord_file
            )

        synced = await self.tree.sync(guild=GUILD_OBJ)
        print(f"Synced {len(synced)} guild command(s) to guild {GUILD_ID}")

bot = LeagueBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user} ({bot.user.id})")

bot.run(TOKEN)
