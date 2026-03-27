import os
import random
import string
import traceback
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

VALID_CLASSES = {"SM85", "SM125", "250F", "450"}
VALID_SERIES = {"MXGP", "SMX"}
VALID_STAGES = {"qualifying", "heat1", "heat2", "final"}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True


def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def make_password(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def ama_points(position: int) -> int:
    table = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18,
        6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8,
        16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1
    }
    return table.get(position, 0)


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


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if RACE_DIRECTOR_ROLE_ID and any(str(r.id) == str(RACE_DIRECTOR_ROLE_ID) for r in member.roles):
        return True
    if ADMIN_ROLE_IDS and any(str(r.id) in ADMIN_ROLE_IDS for r in member.roles):
        return True
    return False


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
                    suspended BOOLEAN DEFAULT FALSE,
                    suspension_reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    track TEXT NOT NULL DEFAULT 'TBD',
                    class_name TEXT DEFAULT '450',
                    season TEXT DEFAULT 'S1',
                    round_number INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'open',
                    event_type TEXT DEFAULT 'practice',
                    requires_guid BOOLEAN DEFAULT FALSE,
                    race_password TEXT,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    series TEXT DEFAULT 'MXGP',
                    race_stage TEXT DEFAULT 'qualifying',
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
                CREATE TABLE IF NOT EXISTS results (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(event_id, rider_id),
                    UNIQUE(event_id, position)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS event_passwords (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    password TEXT NOT NULL,
                    created_by TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(event_id)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS qualifying_times (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    lap_time_ms INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(event_id, rider_id)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS gate_orders (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    rider_id INTEGER NOT NULL REFERENCES riders(id) ON DELETE CASCADE,
                    gate_order INTEGER NOT NULL,
                    source_stage TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(event_id, rider_id),
                    UNIQUE(event_id, gate_order)
                );
            """)

            conn.commit()


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
            class_name="SM85, SM125, 250F, 450"
        )
        async def register_mxb(
            interaction: discord.Interaction,
            mxb_name: str,
            steam_id: str,
            guid: str,
            class_name: str = "450"
        ):
            await interaction.response.defer(ephemeral=True)
            try:
                class_name = class_name.upper().strip()
                if class_name not in VALID_CLASSES:
                    await interaction.followup.send("❌ Class must be SM85, SM125, 250F, or 450.", ephemeral=True)
                    return

                discord_id = str(interaction.user.id)
                discord_name = str(interaction.user)

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT id FROM riders WHERE guid = %s AND discord_id <> %s",
                            (guid, discord_id)
                        )
                        if cur.fetchone():
                            await interaction.followup.send("❌ That GUID is already linked to another rider.", ephemeral=True)
                            return

                        cur.execute("""
                            INSERT INTO riders (
                                discord_id, discord_name, mxb_name, steam_id, guid,
                                guid_status, approved, suspended, suspension_reason, class_name
                            )
                            VALUES (%s,%s,%s,%s,%s,'pending',FALSE,FALSE,NULL,%s)
                            ON CONFLICT (discord_id) DO UPDATE SET
                                discord_name = EXCLUDED.discord_name,
                                mxb_name = EXCLUDED.mxb_name,
                                steam_id = EXCLUDED.steam_id,
                                guid = EXCLUDED.guid,
                                guid_status = 'pending',
                                approved = FALSE,
                                class_name = EXCLUDED.class_name
                        """, (discord_id, discord_name, mxb_name, steam_id, guid, class_name))
                        conn.commit()

                try:
                    if interaction.guild:
                        me_member = interaction.guild.me
                        if me_member and me_member.guild_permissions.manage_nicknames:
                            await interaction.user.edit(nick=mxb_name)
                except Exception:
                    pass

                await interaction.followup.send(
                    f"✅ Registered as **{mxb_name}** in class **{class_name}**.\nGUID is pending approval.",
                    ephemeral=True
                )
            except Exception as e:
                print("register_mxb error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ register_mxb failed: {e}", ephemeral=True)

        @self.tree.command(name="guid_status", description="Check your GUID approval status", guild=GUILD_OBJ)
        async def guid_status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT mxb_name, steam_id, guid, guid_status, class_name, approved
                            FROM riders
                            WHERE discord_id = %s
                        """, (str(interaction.user.id),))
                        rider = cur.fetchone()

                if not rider:
                    await interaction.followup.send("❌ You are not registered yet. Use /register_mxb first.", ephemeral=True)
                    return

                await interaction.followup.send(
                    f"**MXB Name:** {rider['mxb_name'] or 'Not set'}\n"
                    f"**Steam ID:** {rider['steam_id'] or 'Not set'}\n"
                    f"**GUID:** {rider['guid'] or 'Not set'}\n"
                    f"**GUID Status:** {rider['guid_status']}\n"
                    f"**Class:** {rider['class_name'] or '-'}\n"
                    f"**Approved:** {rider['approved']}",
                    ephemeral=True
                )
            except Exception as e:
                print("guid_status error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ guid_status failed: {e}", ephemeral=True)

        @self.tree.command(name="my_profile", description="Show your saved profile", guild=GUILD_OBJ)
        async def my_profile(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT mxb_name, steam_id, guid, guid_status, team_name, rider_number, class_name,
                                   suspended, suspension_reason
                            FROM riders
                            WHERE discord_id = %s
                        """, (str(interaction.user.id),))
                        rider = cur.fetchone()

                if not rider:
                    await interaction.followup.send("❌ No profile found yet. Use /register_mxb first.", ephemeral=True)
                    return

                await interaction.followup.send(
                    f"**MXB Name:** {rider['mxb_name'] or '-'}\n"
                    f"**Steam ID:** {rider['steam_id'] or '-'}\n"
                    f"**GUID:** {rider['guid'] or '-'}\n"
                    f"**GUID Status:** {rider['guid_status'] or '-'}\n"
                    f"**Team:** {rider['team_name'] or '-'}\n"
                    f"**Rider Number:** {rider['rider_number'] or '-'}\n"
                    f"**Class:** {rider['class_name'] or '-'}\n"
                    f"**Suspended:** {rider['suspended']}\n"
                    f"**Suspension Reason:** {rider['suspension_reason'] or '-'}",
                    ephemeral=True
                )
            except Exception as e:
                print("my_profile error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ my_profile failed: {e}", ephemeral=True)

        @self.tree.command(name="set_guid_status", description="Approve or reject a rider GUID", guild=GUILD_OBJ)
        @app_commands.describe(member="Discord member", status="approved, mismatch, rejected, pending")
        async def set_guid_status(interaction: discord.Interaction, member: discord.Member, status: str):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                allowed = {"approved", "mismatch", "rejected", "pending"}
                if status not in allowed:
                    await interaction.followup.send("❌ Status must be approved, mismatch, rejected, or pending.", ephemeral=True)
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

                await interaction.followup.send(f"✅ {member.mention} GUID status set to **{status}**.", ephemeral=True)
            except Exception as e:
                print("set_guid_status error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ set_guid_status failed: {e}", ephemeral=True)

        @self.tree.command(name="create_event", description="Create a race event", guild=GUILD_OBJ)
        @app_commands.describe(
            name="Event name",
            track="Track name",
            series="MXGP or SMX",
            class_name="SM85, SM125, 250F, 450",
            event_type="practice, qualifier, finals",
            race_stage="qualifying, heat1, heat2, final"
        )
        async def create_event(
            interaction: discord.Interaction,
            name: str,
            track: str,
            series: str = "MXGP",
            class_name: str = "450",
            event_type: str = "practice",
            race_stage: str = "qualifying"
        ):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                series = series.upper().strip()
                class_name = class_name.upper().strip()
                event_type = event_type.lower().strip()
                race_stage = race_stage.lower().strip()

                if series not in VALID_SERIES:
                    await interaction.followup.send("❌ Series must be MXGP or SMX.", ephemeral=True)
                    return
                if class_name not in VALID_CLASSES:
                    await interaction.followup.send("❌ Class must be SM85, SM125, 250F, or 450.", ephemeral=True)
                    return
                if event_type not in {"practice", "qualifier", "finals"}:
                    await interaction.followup.send("❌ event_type must be practice, qualifier, or finals.", ephemeral=True)
                    return
                if race_stage not in VALID_STAGES:
                    await interaction.followup.send("❌ race_stage must be qualifying, heat1, heat2, or final.", ephemeral=True)
                    return

                requires_guid = event_type in {"qualifier", "finals"}

                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO events (
                                name, track, season, status, event_type, requires_guid,
                                series, class_name, race_stage
                            )
                            VALUES (%s,%s,%s,'open',%s,%s,%s,%s,%s)
                            RETURNING id
                        """, (
                            name, track, DEFAULT_SEASON, event_type, requires_guid,
                            series, class_name, race_stage
                        ))
                        event_id = cur.fetchone()[0]
                        conn.commit()

                announcement_warning = ""
                if RACE_ANNOUNCEMENTS_CHANNEL_ID:
                    try:
                        channel = self.get_channel(int(RACE_ANNOUNCEMENTS_CHANNEL_ID))
                        if channel:
                            await channel.send(
                                f"🏁 **New Event Created**\n"
                                f"**ID:** {event_id}\n"
                                f"**Name:** {name}\n"
                                f"**Series:** {series}\n"
                                f"**Class:** {class_name}\n"
                                f"**Stage:** {race_stage}\n"
                                f"**Track:** {track}\n"
                                f"**Type:** {event_type}\n"
                                f"**GUID Lock:** {'Yes' if requires_guid else 'No'}"
                            )
                        else:
                            announcement_warning = "\n⚠️ Event created, but announcement channel not found."
                    except Exception as send_error:
                        announcement_warning = f"\n⚠️ Event created, but announcement send failed: {send_error}"

                await interaction.followup.send(
                    f"✅ Event created with ID **{event_id}**.{announcement_warning}",
                    ephemeral=True
                )
            except Exception as e:
                print("create_event error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ create_event failed: {e}", ephemeral=True)

        @self.tree.command(name="list_events", description="List current events", guild=GUILD_OBJ)
        async def list_events(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT id, name, track, series, class_name, race_stage, event_type, status, requires_guid
                            FROM events
                            ORDER BY id DESC
                            LIMIT 20
                        """)
                        events = cur.fetchall()

                if not events:
                    await interaction.followup.send("No events found.", ephemeral=True)
                    return

                lines = []
                for e in events:
                    lines.append(
                        f"**#{e['id']}** — {e['name']} | {e['series']} | {e['class_name']} | {e['race_stage']} | {e['track']} | {e['status']}"
                    )
                await interaction.followup.send("\n".join(lines), ephemeral=True)
            except Exception as e:
                print("list_events error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ list_events failed: {e}", ephemeral=True)

        @self.tree.command(name="join_race", description="Join an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def join_race(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(interaction.user.id),))
                        rider = cur.fetchone()
                        if not rider:
                            await interaction.followup.send("❌ You must register first with /register_mxb.", ephemeral=True)
                            return

                        if rider.get("suspended"):
                            await interaction.followup.send(
                                f"❌ You are suspended. Reason: {rider.get('suspension_reason') or 'No reason set'}",
                                ephemeral=True
                            )
                            return

                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        if rider["class_name"] != event["class_name"]:
                            await interaction.followup.send(
                                f"❌ Wrong class. Event is **{event['class_name']}** and you are **{rider['class_name']}**.",
                                ephemeral=True
                            )
                            return

                        if event["requires_guid"] and rider["guid_status"] != "approved":
                            await interaction.followup.send("❌ This event is GUID locked. Your GUID is not approved yet.", ephemeral=True)
                            return

                        cur.execute("""
                            INSERT INTO registrations (event_id, rider_id)
                            VALUES (%s,%s)
                            ON CONFLICT (event_id, rider_id) DO NOTHING
                        """, (event_id, rider["id"]))
                        conn.commit()

                await interaction.followup.send(f"✅ Joined event **#{event_id}**.", ephemeral=True)
            except Exception as e:
                print("join_race error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ join_race failed: {e}", ephemeral=True)

        @self.tree.command(name="set_qualy_time", description="Set qualifying lap time", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID", member="Rider", lap_time="Example 1:02.341")
        async def set_qualy_time(interaction: discord.Interaction, event_id: int, member: discord.Member, lap_time: str):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                ms = parse_lap_time_to_ms(lap_time)

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(member.id),))
                        rider = cur.fetchone()
                        if not rider:
                            await interaction.followup.send("❌ Rider not registered.", ephemeral=True)
                            return

                        cur.execute("""
                            INSERT INTO qualifying_times (event_id, rider_id, lap_time_ms)
                            VALUES (%s,%s,%s)
                            ON CONFLICT (event_id, rider_id) DO UPDATE SET
                                lap_time_ms = EXCLUDED.lap_time_ms
                        """, (event_id, rider["id"], ms))
                        conn.commit()

                await interaction.followup.send(
                    f"✅ Qualifying time set for **{rider['mxb_name'] or member.display_name}**: **{lap_time}**",
                    ephemeral=True
                )
            except Exception as e:
                print("set_qualy_time error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ set_qualy_time failed: {e}", ephemeral=True)

        @self.tree.command(name="build_gate_order", description="Build gate order for an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def build_gate_order(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        stage = event["race_stage"]
                        cur.execute("DELETE FROM gate_orders WHERE event_id = %s", (event_id,))

                        if stage in {"qualifying", "heat1"}:
                            cur.execute("""
                                SELECT r.id AS rider_id, r.mxb_name, q.lap_time_ms
                                FROM qualifying_times q
                                JOIN riders r ON r.id = q.rider_id
                                WHERE q.event_id = %s
                                ORDER BY q.lap_time_ms ASC, r.mxb_name ASC
                            """, (event_id,))
                            rows = cur.fetchall()
                            source_stage = "qualifying"

                        elif stage == "heat2":
                            cur.execute("""
                                SELECT r.id AS rider_id, r.mxb_name, res.position
                                FROM results res
                                JOIN riders r ON r.id = res.rider_id
                                WHERE res.event_id = %s
                                ORDER BY res.position ASC, r.mxb_name ASC
                            """, (event_id,))
                            rows = cur.fetchall()
                            source_stage = "heat1"

                        elif stage == "final":
                            cur.execute("""
                                SELECT r.id AS rider_id, r.mxb_name, res.position
                                FROM results res
                                JOIN riders r ON r.id = res.rider_id
                                WHERE res.event_id = %s
                                ORDER BY res.position ASC, r.mxb_name ASC
                            """, (event_id,))
                            rows = cur.fetchall()
                            source_stage = "heat2"
                        else:
                            await interaction.followup.send("❌ Invalid race stage.", ephemeral=True)
                            return

                        if not rows:
                            await interaction.followup.send("❌ No source data found to build gate order.", ephemeral=True)
                            return

                        for idx, row in enumerate(rows, start=1):
                            cur.execute("""
                                INSERT INTO gate_orders (event_id, rider_id, gate_order, source_stage)
                                VALUES (%s,%s,%s,%s)
                            """, (event_id, row["rider_id"], idx, source_stage))

                        conn.commit()

                await interaction.followup.send(
                    f"✅ Gate order built for event **#{event_id}** using **{source_stage}** order.",
                    ephemeral=True
                )
            except Exception as e:
                print("build_gate_order error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ build_gate_order failed: {e}", ephemeral=True)

        @self.tree.command(name="view_gates", description="Show gate order", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def view_gates(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT r.mxb_name, g.gate_order, g.source_stage
                            FROM gate_orders g
                            JOIN riders r ON r.id = g.rider_id
                            WHERE g.event_id = %s
                            ORDER BY g.gate_order ASC
                        """, (event_id,))
                        rows = cur.fetchall()

                if not rows:
                    await interaction.followup.send("No gate order saved for this event.", ephemeral=True)
                    return

                lines = [f"{row['gate_order']}. {row['mxb_name']} ({row['source_stage']})" for row in rows]
                await interaction.followup.send("🏁 **Gate Order**\n" + "\n".join(lines), ephemeral=True)
            except Exception as e:
                print("view_gates error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ view_gates failed: {e}", ephemeral=True)

        @self.tree.command(name="start_race", description="Start race and DM password to joined riders", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def start_race(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        password = make_password()

                        cur.execute("""
                            INSERT INTO event_passwords (event_id, password, created_by)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (event_id) DO UPDATE SET
                                password = EXCLUDED.password,
                                created_by = EXCLUDED.created_by,
                                created_at = NOW()
                        """, (event_id, password, str(interaction.user.id)))

                        cur.execute("""
                            UPDATE events
                            SET status = 'started',
                                race_password = %s,
                                started_at = NOW()
                            WHERE id = %s
                        """, (password, event_id))

                        cur.execute("""
                            SELECT r.discord_id, r.mxb_name
                            FROM registrations reg
                            JOIN riders r ON r.id = reg.rider_id
                            WHERE reg.event_id = %s
                        """, (event_id,))
                        riders = cur.fetchall()

                        conn.commit()

                sent = 0
                failed = 0

                for rider in riders:
                    try:
                        user = await self.fetch_user(int(rider["discord_id"]))
                        await user.send(
                            f"🏁 **Race Started**\n"
                            f"**Event:** {event['name']}\n"
                            f"**Track:** {event['track']}\n"
                            f"**Password:** `{password}`"
                        )
                        sent += 1
                    except Exception:
                        failed += 1

                await interaction.followup.send(
                    f"✅ Race started for event **#{event_id}**\n"
                    f"🔐 Password: `{password}`\n"
                    f"📨 DMs sent: **{sent}**\n"
                    f"⚠️ DM failed: **{failed}**",
                    ephemeral=True
                )
            except Exception as e:
                print("start_race error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ start_race failed: {e}", ephemeral=True)

        @self.tree.command(name="race_info", description="Show race info for an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def race_info(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT id, name, track, status, event_type, requires_guid, race_password,
                                   started_at, ended_at, series, class_name, race_stage
                            FROM events
                            WHERE id = %s
                        """, (event_id,))
                        event = cur.fetchone()

                if not event:
                    await interaction.followup.send("❌ Event not found.", ephemeral=True)
                    return

                await interaction.followup.send(
                    f"🏁 **Race Info**\n"
                    f"**ID:** {event['id']}\n"
                    f"**Name:** {event['name']}\n"
                    f"**Series:** {event['series']}\n"
                    f"**Class:** {event['class_name']}\n"
                    f"**Stage:** {event['race_stage']}\n"
                    f"**Track:** {event['track']}\n"
                    f"**Type:** {event['event_type']}\n"
                    f"**Status:** {event['status']}\n"
                    f"**GUID Lock:** {'Yes' if event['requires_guid'] else 'No'}\n"
                    f"**Password:** {event['race_password'] or 'Not started'}",
                    ephemeral=True
                )
            except Exception as e:
                print("race_info error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ race_info failed: {e}", ephemeral=True)

        @self.tree.command(name="end_race", description="End a race event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def end_race(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE events
                            SET status = 'finished',
                                ended_at = NOW()
                            WHERE id = %s
                        """, (event_id,))
                        conn.commit()

                await interaction.followup.send(f"✅ Event **#{event_id}** ended.", ephemeral=True)
            except Exception as e:
                print("end_race error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ end_race failed: {e}", ephemeral=True)

        @self.tree.command(name="set_result", description="Set race result for a rider", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID", member="Rider", position="Finishing position")
        async def set_result(interaction: discord.Interaction, event_id: int, member: discord.Member, position: int):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                if position < 1:
                    await interaction.followup.send("❌ Position must be 1 or higher.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(member.id),))
                        rider = cur.fetchone()
                        if not rider:
                            await interaction.followup.send("❌ Rider not registered.", ephemeral=True)
                            return

                        points = ama_points(position)

                        cur.execute("""
                            INSERT INTO results (event_id, rider_id, position, points)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (event_id, rider_id) DO UPDATE SET
                                position = EXCLUDED.position,
                                points = EXCLUDED.points
                        """, (event_id, rider["id"], position, points))
                        conn.commit()

                await interaction.followup.send(
                    f"✅ Result saved.\n"
                    f"Rider: **{rider['mxb_name'] or member.display_name}**\n"
                    f"Position: **{position}**\n"
                    f"Points: **{points}**",
                    ephemeral=True
                )
            except Exception as e:
                print("set_result error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ set_result failed: {e}", ephemeral=True)

        @self.tree.command(name="event_results", description="Show results for an event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID")
        async def event_results(interaction: discord.Interaction, event_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                        event = cur.fetchone()
                        if not event:
                            await interaction.followup.send("❌ Event not found.", ephemeral=True)
                            return

                        cur.execute("""
                            SELECT r.mxb_name, res.position, res.points
                            FROM results res
                            JOIN riders r ON r.id = res.rider_id
                            WHERE res.event_id = %s
                            ORDER BY res.position ASC
                        """, (event_id,))
                        rows = cur.fetchall()

                if not rows:
                    await interaction.followup.send("No results saved for this event yet.", ephemeral=True)
                    return

                lines = [f"{row['position']}. **{row['mxb_name'] or 'Unknown'}** — {row['points']} pts" for row in rows]
                await interaction.followup.send(
                    f"🏁 **Results for Event #{event_id} — {event['name']}**\n" + "\n".join(lines),
                    ephemeral=True
                )
            except Exception as e:
                print("event_results error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ event_results failed: {e}", ephemeral=True)

        @self.tree.command(name="leaderboard", description="Show current leaderboard", guild=GUILD_OBJ)
        async def leaderboard(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT
                                r.mxb_name,
                                r.class_name,
                                SUM(res.points) AS total_points
                            FROM results res
                            JOIN riders r ON r.id = res.rider_id
                            GROUP BY r.mxb_name, r.class_name
                            ORDER BY total_points DESC, r.mxb_name ASC
                            LIMIT 50
                        """)
                        rows = cur.fetchall()

                if not rows:
                    await interaction.followup.send("No leaderboard data yet.", ephemeral=True)
                    return

                lines = []
                for i, row in enumerate(rows, start=1):
                    lines.append(
                        f"{i}. **{row['mxb_name'] or 'Unknown'}** | Class: {row['class_name'] or '450'} | Points: {row['total_points']}"
                    )
                await interaction.followup.send("🏆 **Leaderboard**\n" + "\n".join(lines), ephemeral=True)
            except Exception as e:
                print("leaderboard error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ leaderboard failed: {e}", ephemeral=True)

        @self.tree.command(name="disqualify", description="Disqualify rider from event", guild=GUILD_OBJ)
        @app_commands.describe(event_id="Event ID", member="Rider to DQ")
        async def disqualify(interaction: discord.Interaction, event_id: int, member: discord.Member):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM riders WHERE discord_id = %s", (str(member.id),))
                        rider = cur.fetchone()
                        if not rider:
                            await interaction.followup.send("❌ Rider not registered.", ephemeral=True)
                            return

                        cur.execute("DELETE FROM registrations WHERE event_id = %s AND rider_id = %s", (event_id, rider["id"]))
                        cur.execute("DELETE FROM results WHERE event_id = %s AND rider_id = %s", (event_id, rider["id"]))
                        conn.commit()

                await interaction.followup.send(f"✅ {member.mention} disqualified from event **#{event_id}**.", ephemeral=True)
            except Exception as e:
                print("disqualify error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ disqualify failed: {e}", ephemeral=True)

        @self.tree.command(name="suspend_rider", description="Suspend a rider", guild=GUILD_OBJ)
        @app_commands.describe(member="Rider", reason="Suspension reason")
        async def suspend_rider(interaction: discord.Interaction, member: discord.Member, reason: str):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE riders
                            SET suspended = TRUE,
                                suspension_reason = %s
                            WHERE discord_id = %s
                        """, (reason, str(member.id)))
                        conn.commit()

                await interaction.followup.send(f"✅ {member.mention} suspended.\nReason: **{reason}**", ephemeral=True)
            except Exception as e:
                print("suspend_rider error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ suspend_rider failed: {e}", ephemeral=True)

        @self.tree.command(name="unsuspend_rider", description="Remove rider suspension", guild=GUILD_OBJ)
        @app_commands.describe(member="Rider")
        async def unsuspend_rider(interaction: discord.Interaction, member: discord.Member):
            await interaction.response.defer(ephemeral=True)
            try:
                if not is_staff(interaction.user):
                    await interaction.followup.send("❌ No permission.", ephemeral=True)
                    return

                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE riders
                            SET suspended = FALSE,
                                suspension_reason = NULL
                            WHERE discord_id = %s
                        """, (str(member.id),))
                        conn.commit()

                await interaction.followup.send(f"✅ {member.mention} unsuspended.", ephemeral=True)
            except Exception as e:
                print("unsuspend_rider error:")
                traceback.print_exc()
                await interaction.followup.send(f"❌ unsuspend_rider failed: {e}", ephemeral=True)

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
