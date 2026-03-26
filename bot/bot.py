import io
import os
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import psycopg2.extras

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
ADMIN_ROLE_IDS = {int(x.strip()) for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x.strip().isdigit()}
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")

if not DISCORD_BOT_TOKEN or not DATABASE_URL or not DISCORD_GUILD_ID:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN, DATABASE_URL, or DISCORD_GUILD_ID")

GUILD_ID_INT = int(DISCORD_GUILD_ID)
GUILD_OBJ = discord.Object(id=GUILD_ID_INT)
RACE_DIRECTOR_ROLE_ID_INT = int(RACE_DIRECTOR_ROLE_ID) if RACE_DIRECTOR_ROLE_ID else None
RACE_ANNOUNCEMENTS_CHANNEL_ID_INT = int(RACE_ANNOUNCEMENTS_CHANNEL_ID) if RACE_ANNOUNCEMENTS_CHANNEL_ID else None

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
_has_synced = False

def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def db_exec(sql: str, params=None, fetch: str = "none"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "one":
                row = cur.fetchone(); conn.commit(); return row
            if fetch == "all":
                rows = cur.fetchall(); conn.commit(); return rows
            conn.commit(); return None

def init_db():
    db_exec("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_id TEXT UNIQUE,
        discord_name TEXT,
        mxb_name TEXT,
        steam_id TEXT,
        guid TEXT,
        guid_status TEXT DEFAULT 'pending',
        guid_note TEXT,
        team_name TEXT,
        rider_number TEXT,
        rider_class TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid_status TEXT DEFAULT 'pending';")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid_note TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS rider_class TEXT;")
    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_guid_unique ON users(guid) WHERE guid IS NOT NULL;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        name TEXT,
        track TEXT,
        class_name TEXT,
        season TEXT DEFAULT 'S1',
        round_number INTEGER DEFAULT 1,
        event_type TEXT DEFAULT 'practice',
        guid_lock_required BOOLEAN DEFAULT FALSE,
        start_time TIMESTAMP NULL,
        status TEXT DEFAULT 'open',
        created_by_discord_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'practice';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS guid_lock_required BOOLEAN DEFAULT FALSE;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS registrations (
        id SERIAL PRIMARY KEY,
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
        discord_id TEXT,
        rider_name TEXT,
        rider_guid TEXT,
        class_name TEXT,
        team_name TEXT,
        gate_pick INTEGER,
        status TEXT DEFAULT 'registered',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS rider_guid TEXT;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS qualifying_times (
        id SERIAL PRIMARY KEY,
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
        discord_id TEXT,
        rider_name TEXT,
        rider_guid TEXT,
        best_lap_ms INTEGER NOT NULL,
        lap_source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(event_id, rider_guid)
    );
    """)


def is_staff(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    role_ids = {role.id for role in interaction.user.roles}
    if RACE_DIRECTOR_ROLE_ID_INT and RACE_DIRECTOR_ROLE_ID_INT in role_ids:
        return True
    return bool(role_ids & ADMIN_ROLE_IDS) or interaction.user.guild_permissions.administrator

async def announce(text: str):
    if not RACE_ANNOUNCEMENTS_CHANNEL_ID_INT:
        return
    channel = bot.get_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
    if channel:
        await channel.send(text)

async def ensure_ephemeral(interaction: discord.Interaction, text: str):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)

@bot.event
async def on_ready():
    global _has_synced
    init_db()
    if not _has_synced:
        await bot.tree.sync(guild=GUILD_OBJ)
        _has_synced = True
    print(f"BOT ONLINE: {bot.user}")

@bot.tree.command(name="register_mxb", description="Save your MX Bikes profile and GUID", guild=GUILD_OBJ)
@app_commands.describe(mxb_name="Your in-game name", steam_id="Steam ID", guid="Your MX Bikes GUID", rider_class="250 or 450")
async def register_mxb(interaction: discord.Interaction, mxb_name: str, guid: str, steam_id: str | None = None, rider_class: str | None = None, team_name: str | None = None, rider_number: str | None = None):
    guid = guid.strip()
    if len(guid) < 6:
        await interaction.response.send_message("GUID looks too short. Open MX Bikes Profiles and copy the full GUID.", ephemeral=True)
        return
    duplicate = db_exec("SELECT discord_id, discord_name FROM users WHERE guid = %s AND discord_id <> %s LIMIT 1;", (guid, str(interaction.user.id)), fetch="one")
    if duplicate:
        await interaction.response.send_message(f"That GUID is already used by {duplicate['discord_name']}.", ephemeral=True)
        return
    db_exec("""
        INSERT INTO users (discord_id, discord_name, mxb_name, steam_id, guid, guid_status, guid_note, team_name, rider_number, rider_class)
        VALUES (%s,%s,%s,%s,%s,'pending',NULL,%s,%s,%s)
        ON CONFLICT (discord_id) DO UPDATE SET
            discord_name = EXCLUDED.discord_name,
            mxb_name = EXCLUDED.mxb_name,
            steam_id = EXCLUDED.steam_id,
            guid = EXCLUDED.guid,
            guid_status = 'pending',
            guid_note = NULL,
            team_name = EXCLUDED.team_name,
            rider_number = EXCLUDED.rider_number,
            rider_class = EXCLUDED.rider_class;
    """, (str(interaction.user.id), interaction.user.display_name, mxb_name.strip(), (steam_id or '').strip() or None, guid, (team_name or '').strip() or None, (rider_number or '').strip() or None, (rider_class or '').strip() or None))
    await interaction.response.send_message("Profile saved. Your GUID is now pending staff approval.", ephemeral=True)

@bot.tree.command(name="guid_status", description="Check your GUID approval status", guild=GUILD_OBJ)
async def guid_status(interaction: discord.Interaction):
    row = db_exec("SELECT guid, guid_status, guid_note FROM users WHERE discord_id = %s LIMIT 1;", (str(interaction.user.id),), fetch="one")
    if not row:
        await interaction.response.send_message("No profile found. Use /register_mxb first.", ephemeral=True)
        return
    await interaction.response.send_message(f"GUID: `{row.get('guid') or 'missing'}`\nStatus: **{row.get('guid_status') or 'pending'}**\nNote: {row.get('guid_note') or 'None'}", ephemeral=True)

@bot.tree.command(name="set_guid_status", description="Approve or reject a rider GUID", guild=GUILD_OBJ)
@app_commands.describe(member="Rider", status="approved / mismatch / rejected / pending", note="Reason shown to rider")
async def set_guid_status(interaction: discord.Interaction, member: discord.Member, status: str, note: str | None = None):
    if not is_staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    status = status.strip().lower()
    if status not in {"approved", "mismatch", "rejected", "pending"}:
        await interaction.response.send_message("Use approved, mismatch, rejected, or pending.", ephemeral=True)
        return
    db_exec("UPDATE users SET guid_status = %s, guid_note = %s WHERE discord_id = %s;", (status, (note or '').strip() or None, str(member.id)))
    await interaction.response.send_message(f"GUID status for {member.mention} set to **{status}**.", ephemeral=True)

@bot.tree.command(name="create_event", description="Create event", guild=GUILD_OBJ)
async def create_event(interaction: discord.Interaction, name: str, track: str, class_name: str, round_number: int, event_type: str = "practice"):
    if not is_staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    event_type = event_type.lower().strip()
    if event_type not in {"practice", "qualifier", "finals"}:
        event_type = "practice"
    guid_lock_required = event_type in {"qualifier", "finals"}
    row = db_exec("""
        INSERT INTO events (name, track, class_name, season, round_number, event_type, guid_lock_required, status, created_by_discord_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s) RETURNING *;
    """, (name.strip(), track.strip(), class_name.strip(), DEFAULT_SEASON, int(round_number), event_type, guid_lock_required, str(interaction.user.id)), fetch="one")
    await interaction.response.send_message(f"Created event #{row['id']} — {row['name']} ({event_type}).", ephemeral=True)

@bot.tree.command(name="join_race", description="Register into an event", guild=GUILD_OBJ)
async def join_race(interaction: discord.Interaction, event_id: int):
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return
    rider = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (str(interaction.user.id),), fetch="one")
    if not rider:
        await interaction.response.send_message("Use /register_mxb first.", ephemeral=True)
        return
    if event.get("guid_lock_required"):
        if not rider.get("guid"):
            await interaction.response.send_message("GUID lock active: add your GUID in /register_mxb before joining qualifiers/finals.", ephemeral=True)
            return
        if rider.get("guid_status") != "approved":
            await interaction.response.send_message(f"GUID lock active: your GUID status is **{rider.get('guid_status') or 'pending'}**. Fix it before joining.", ephemeral=True)
            return
    existing = db_exec("SELECT id FROM registrations WHERE event_id = %s AND discord_id = %s LIMIT 1;", (event_id, str(interaction.user.id)), fetch="one")
    if existing:
        await interaction.response.send_message("You are already registered.", ephemeral=True)
        return
    db_exec("INSERT INTO registrations (event_id, discord_id, rider_name, rider_guid, class_name, team_name, status) VALUES (%s,%s,%s,%s,%s,%s,'registered');", (event_id, str(interaction.user.id), rider.get('mxb_name'), rider.get('guid'), event.get('class_name'), rider.get('team_name')))
    await interaction.response.send_message(f"Registered for **{event['name']}**.", ephemeral=True)

@bot.tree.command(name="set_qualy_time", description="Add or update a qualifying lap time in milliseconds", guild=GUILD_OBJ)
async def set_qualy_time(interaction: discord.Interaction, event_id: int, member: discord.Member, best_lap_ms: int):
    if not is_staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    rider = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (str(member.id),), fetch="one")
    if not rider or not rider.get("guid"):
        await interaction.response.send_message("That rider has no GUID on file.", ephemeral=True)
        return
    db_exec("""
        INSERT INTO qualifying_times (event_id, discord_id, rider_name, rider_guid, best_lap_ms, lap_source)
        VALUES (%s,%s,%s,%s,%s,'manual')
        ON CONFLICT (event_id, rider_guid) DO UPDATE SET
            discord_id = EXCLUDED.discord_id,
            rider_name = EXCLUDED.rider_name,
            best_lap_ms = EXCLUDED.best_lap_ms,
            lap_source = 'manual';
    """, (event_id, str(member.id), rider.get('mxb_name'), rider.get('guid'), best_lap_ms))
    await interaction.response.send_message("Qualifying time saved.", ephemeral=True)

@bot.tree.command(name="gate_order", description="Show fastest-lap gate order", guild=GUILD_OBJ)
async def gate_order(interaction: discord.Interaction, event_id: int):
    rows = db_exec("SELECT rider_name, rider_guid, best_lap_ms FROM qualifying_times WHERE event_id = %s ORDER BY best_lap_ms ASC, created_at ASC;", (event_id,), fetch="all")
    if not rows:
        await interaction.response.send_message("No qualifying times saved yet.", ephemeral=True)
        return
    lines = []
    for i, row in enumerate(rows, start=1):
        mins = row['best_lap_ms'] // 60000
        secs = (row['best_lap_ms'] % 60000) / 1000
        lines.append(f"**{i}.** {row['rider_name']} — `{mins}:{secs:06.3f}`")
        db_exec("UPDATE registrations SET gate_pick = %s WHERE event_id = %s AND rider_guid = %s;", (i, event_id, row['rider_guid']))
    await interaction.response.send_message("Fastest-lap gate order:\n" + "\n".join(lines), ephemeral=False)

@bot.tree.command(name="export_whitelist", description="Export GUID whitelist for an event", guild=GUILD_OBJ)
async def export_whitelist(interaction: discord.Interaction, event_id: int):
    if not is_staff(interaction):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    rows = db_exec("""
        SELECT u.guid, u.mxb_name, u.rider_number
        FROM registrations r
        JOIN users u ON u.discord_id = r.discord_id
        WHERE r.event_id = %s AND COALESCE(u.guid_status,'pending') = 'approved' AND u.guid IS NOT NULL
        ORDER BY COALESCE(r.gate_pick, 9999), u.mxb_name ASC;
    """, (event_id,), fetch="all")
    if not rows:
        await interaction.response.send_message("No approved GUID riders found for this event.", ephemeral=True)
        return
    chunks = []
    for i, row in enumerate(rows):
        chunks.append(f"[entry{i}]\nguid = {row['guid']}\nrace_number = {row.get('rider_number') or ''}\nname = {row.get('mxb_name') or ''}\n")
    data = "\n".join(chunks).encode("utf-8")
    await interaction.response.send_message(
        f"Whitelist for **{event['name']}**. Put this file beside your server config and set `whitelist = whitelist_event_{event_id}.txt` in server.ini.",
        file=discord.File(io.BytesIO(data), filename=f"whitelist_event_{event_id}.txt"),
        ephemeral=True,
    )

bot.run(DISCORD_BOT_TOKEN)

