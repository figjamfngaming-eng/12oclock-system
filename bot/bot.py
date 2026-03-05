import os
import sys
from pathlib import Path

# ✅ Make sibling folders importable (shared/) even when Render Root Directory = bot
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import discord
from discord import app_commands
from discord.ext import commands

from shared.db import init_db, q, exec1


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = os.getenv("GUILD_ID", "")  # optional: speeds up slash command updates
ANNOUNCE_CHANNEL_ID = os.getenv("ANNOUNCE_CHANNEL_ID", "")
RACE_DIRECTOR_ROLE_ID = os.getenv("ROLE_RACE_DIRECTOR_ID", "")

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://one2oclock-system.onrender.com").rstrip("/")


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def is_race_director(member: discord.Member) -> bool:
    if not RACE_DIRECTOR_ROLE_ID:
        return False
    return any(str(r.id) == str(RACE_DIRECTOR_ROLE_ID) for r in member.roles)


@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user}")

    # ✅ init DB safely
    try:
        init_db()
    except Exception as e:
        print("[WARN] init_db failed:", e)

    # ✅ Slash command sync
    try:
        if GUILD_ID and str(GUILD_ID).isdigit():
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("[BOT] Synced commands to guild", GUILD_ID)
        else:
            await bot.tree.sync()
            print("[BOT] Synced global commands")
    except Exception as e:
        print("[BOT] Sync error:", e)


# ----------------------------
# /create_event (Race Director)
# ----------------------------
@bot.tree.command(name="create_event", description="Create a new race event (Race Director only).")
@app_commands.describe(
    title="Event title (e.g. Round 1)",
    mode="MX or SX",
    bike_class="450 / 250 / 2T",
    season="Season number",
    start_ts="Start time (ISO) e.g. 2026-03-10 20:00",
    track="Track name",
    notes="Notes",
)
async def create_event(
    interaction: discord.Interaction,
    title: str,
    mode: str,
    bike_class: str,
    season: int,
    start_ts: str,
    track: str,
    notes: str = "",
):
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Run this inside the server.", ephemeral=True)

    if not is_race_director(interaction.user):
        return await interaction.response.send_message("Race Director only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    # store event
    row = exec1(
        "INSERT INTO events (mode, class, title, season, start_ts, track, notes) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (mode.upper(), bike_class, title, season, start_ts, track, notes),
    )
    event_id = row["id"]

    # announce
    msg = (
        f"🏁 **{mode.upper()} {bike_class} — {title}**\n"
        f"Season: **{season}**\n"
        f"Start: **{start_ts}**\n"
        f"Track: **{track}**\n"
        f"Notes: {notes or '-'}\n\n"
        f"Event ID: **{event_id}**\n"
        f"Upload results: {WEBSITE_URL}/upload\n"
        f"Event page: {WEBSITE_URL}/events/{event_id}\n"
    )

    if ANNOUNCE_CHANNEL_ID and str(ANNOUNCE_CHANNEL_ID).isdigit():
        ch = bot.get_channel(int(ANNOUNCE_CHANNEL_ID))
        if ch:
            await ch.send(msg)

    await interaction.followup.send(f"✅ Event created (ID {event_id}).", ephemeral=True)


# ----------------------------
# /standings (this fixes your “standing” issue)
# ----------------------------
@bot.tree.command(name="standings", description="Show standings link (and top 10) for a class.")
@app_commands.describe(bike_class="450 / 250 / 2T")
async def standings(interaction: discord.Interaction, bike_class: str):
    await interaction.response.defer(ephemeral=False)

    # Pull top 10 from DB (bot reads same DB as web)
    rows = q(
        "SELECT u.mxb_name, u.discord_name, COALESCE(SUM(r.points),0) AS points "
        "FROM users u "
        "JOIN results r ON r.user_id=u.id "
        "JOIN events e ON e.id=r.event_id "
        "WHERE e.class=%s "
        "GROUP BY u.mxb_name, u.discord_name "
        "ORDER BY points DESC, u.mxb_name ASC "
        "LIMIT 10",
        (bike_class,),
    )

    lines = []
    for i, r in enumerate(rows, start=1):
        name = r["mxb_name"] or r["discord_name"] or "Unknown"
        lines.append(f"**{i}.** {name} — **{int(r['points'])}** pts")

    text = (
        f"🏆 **Standings ({bike_class})**\n"
        f"{WEBSITE_URL}/standings?class={bike_class}\n\n"
        + ("\n".join(lines) if lines else "_No points yet for this class._")
    )

    await interaction.followup.send(text)


# ----------------------------
# /penalty (Race Director)
# ----------------------------
@bot.tree.command(name="penalty", description="Apply a points penalty to a rider (Race Director only).")
@app_commands.describe(event_id="Event ID", mxb_name="Rider MXB name", points="Negative number e.g. -5", reason="Reason")
async def penalty(interaction: discord.Interaction, event_id: int, mxb_name: str, points: int, reason: str = ""):
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Run this inside the server.", ephemeral=True)

    if not is_race_director(interaction.user):
        return await interaction.response.send_message("Race Director only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    user = q("SELECT id FROM users WHERE LOWER(mxb_name)=LOWER(%s) LIMIT 1", (mxb_name.strip(),))
    if not user:
        return await interaction.followup.send("❌ Rider not found (MXB name must match profile).", ephemeral=True)

    uid = user[0]["id"]

    # upsert penalty row (store as result row with negative points if no result exists)
    existing = q("SELECT id, points FROM results WHERE event_id=%s AND user_id=%s", (event_id, uid))
    if existing:
        new_points = int(existing[0]["points"]) + int(points)
        exec1("UPDATE results SET points=%s WHERE id=%s", (new_points, existing[0]["id"]))
    else:
        exec1(
            "INSERT INTO results (event_id, user_id, position, points, raw_json) VALUES (%s,%s,%s,%s,%s)",
            (event_id, uid, 999, int(points), f"penalty:{reason}"),
        )

    await interaction.followup.send(f"✅ Penalty applied to **{mxb_name}** ({points} pts). {reason}", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN env var")
    bot.run(DISCORD_TOKEN)
