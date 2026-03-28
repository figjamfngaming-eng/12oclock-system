import os
import discord
from discord.ext import commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))

ROLE_LINKED_ID = int(os.getenv("ROLE_LINKED_ID", "0"))
ROLE_SM85_ID = int(os.getenv("ROLE_SM85_ID", "0"))
ROLE_SM125_ID = int(os.getenv("ROLE_SM125_ID", "0"))
ROLE_250F_ID = int(os.getenv("ROLE_250F_ID", "0"))
ROLE_450_ID = int(os.getenv("ROLE_450_ID", "0"))

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not DISCORD_GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID is missing")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def points_for_position(position: int) -> int:
    table = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18,
        6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8,
        16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1
    }
    return table.get(position, 0)


def class_role_id(class_name: str) -> int:
    class_name = (class_name or "").upper()
    if class_name == "SM85":
        return ROLE_SM85_ID
    if class_name == "SM125":
        return ROLE_SM125_ID
    if class_name == "250F":
        return ROLE_250F_ID
    if class_name == "450":
        return ROLE_450_ID
    return 0


async def sync_member_roles_for_discord_user(discord_user_id: str):
    guild = bot.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        guild = await bot.fetch_guild(DISCORD_GUILD_ID)

    member = guild.get_member(int(discord_user_id))
    if member is None:
        member = await guild.fetch_member(int(discord_user_id))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    al.discord_id,
                    al.discord_username,
                    al.approved AS link_approved,
                    r.class_name,
                    r.approved AS rider_approved
                FROM account_links al
                LEFT JOIN riders r ON r.discord_user_id = al.discord_id
                WHERE al.discord_id = %s
            """, (discord_user_id,))
            row = cur.fetchone()

    if not row:
        return False, "No linked account found."

    if not row["link_approved"]:
        return False, "Link is not approved yet."

    roles_to_add = []
    roles_to_remove = []

    linked_role = guild.get_role(ROLE_LINKED_ID) if ROLE_LINKED_ID else None
    class_role = guild.get_role(class_role_id(row["class_name"])) if class_role_id(row["class_name"]) else None

    if linked_role and linked_role not in member.roles:
        roles_to_add.append(linked_role)

    class_role_ids = {ROLE_SM85_ID, ROLE_SM125_ID, ROLE_250F_ID, ROLE_450_ID}
    for role in member.roles:
        if role.id in class_role_ids and (not class_role or role.id != class_role.id):
            roles_to_remove.append(role)

    if class_role and class_role not in member.roles:
        roles_to_add.append(class_role)

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Updating linked class role")

    if roles_to_add:
        await member.add_roles(*roles_to_add, reason="Approved website account link")

    return True, "Roles synced."


@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong! Bot is working")


@bot.command()
async def register(ctx, name: str, guid: str, cls: str):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO riders (discord_id, discord_user_id, mxb_name, guid, class_name, approved)
                    VALUES (%s, %s, %s, %s, %s, FALSE)
                    ON CONFLICT (discord_id) DO UPDATE SET
                        discord_user_id = EXCLUDED.discord_user_id,
                        mxb_name = EXCLUDED.mxb_name,
                        guid = EXCLUDED.guid,
                        class_name = EXCLUDED.class_name
                """, (str(ctx.author.id), str(ctx.author.id), name, guid, cls))
                conn.commit()

        await ctx.send(f"✅ Registered {name} in class {cls}")
    except Exception as e:
        await ctx.send(f"❌ register failed: {e}")


@bot.command()
async def approve_link(ctx, discord_user_id: str):
    try:
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Admin only.")
            return

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE account_links
                    SET approved = TRUE, link_status = 'approved'
                    WHERE discord_id = %s
                """, (discord_user_id,))
                cur.execute("""
                    UPDATE riders
                    SET approved = TRUE, discord_user_id = %s
                    WHERE discord_id = %s
                """, (discord_user_id, discord_user_id))
                conn.commit()

        ok, msg = await sync_member_roles_for_discord_user(discord_user_id)
        if ok:
            await ctx.send(f"✅ Link approved and roles synced for {discord_user_id}")
        else:
            await ctx.send(f"⚠️ Link approved but role sync issue: {msg}")
    except Exception as e:
        await ctx.send(f"❌ approve_link failed: {e}")


@bot.command()
async def sync_roles(ctx, discord_user_id: str = None):
    try:
        target_id = discord_user_id or str(ctx.author.id)
        ok, msg = await sync_member_roles_for_discord_user(target_id)
        if ok:
            await ctx.send(f"✅ Roles synced for {target_id}")
        else:
            await ctx.send(f"❌ sync_roles failed: {msg}")
    except Exception as e:
        await ctx.send(f"❌ sync_roles failed: {e}")


@bot.command()
async def create_event(ctx, *, args: str):
    try:
        parts = args.rsplit(" ", 1)
        if len(parts) != 2:
            await ctx.send("Usage: !create_event Event Name 450")
            return

        name, cls = parts[0], parts[1]

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO events (name, class_name, race_stage)
                    VALUES (%s, %s, 'qualifying')
                """, (name, cls))
                conn.commit()

        await ctx.send(f"✅ Event created: {name} ({cls})")
    except Exception as e:
        await ctx.send(f"❌ create_event failed: {e}")


@bot.command()
async def join(ctx, event_id: int):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, approved, class_name
                    FROM riders
                    WHERE discord_id = %s
                """, (str(ctx.author.id),))
                rider = cur.fetchone()

                if not rider:
                    await ctx.send("❌ Register first with !register")
                    return

                if not rider["approved"]:
                    await ctx.send("❌ Your account is not linked/approved yet.")
                    return

                cur.execute("""
                    SELECT class_name
                    FROM events
                    WHERE id = %s
                """, (event_id,))
                event = cur.fetchone()

                if not event:
                    await ctx.send("❌ Event not found.")
                    return

                if (event["class_name"] or "").upper() != (rider["class_name"] or "").upper():
                    await ctx.send(f"❌ Wrong class. Event is {event['class_name']}, you are {rider['class_name']}.")
                    return

                cur.execute("""
                    INSERT INTO registrations (event_id, rider_id)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id, rider_id) DO NOTHING
                """, (event_id, rider["id"]))
                conn.commit()

        await ctx.send(f"✅ Joined event #{event_id}")
    except Exception as e:
        await ctx.send(f"❌ join failed: {e}")


@bot.command()
async def gate(ctx, event_id: int, rider_id: int, gate_order: int):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO gate_orders (event_id, rider_id, gate_order)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (event_id, rider_id) DO UPDATE SET
                        gate_order = EXCLUDED.gate_order
                """, (event_id, rider_id, gate_order))
                conn.commit()

        await ctx.send(f"✅ Gate saved: rider #{rider_id} -> gate {gate_order}")
    except Exception as e:
        await ctx.send(f"❌ gate failed: {e}")


@bot.command()
async def result(ctx, event_id: int, rider_id: int, position: int):
    try:
        pts = points_for_position(position)

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO results (event_id, rider_id, position, points)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id, rider_id) DO UPDATE SET
                        position = EXCLUDED.position,
                        points = EXCLUDED.points
                """, (event_id, rider_id, position, pts))
                conn.commit()

        await ctx.send(f"✅ Result saved | Rider #{rider_id} | Pos {position} | Points {pts}")
    except Exception as e:
        await ctx.send(f"❌ result failed: {e}")


@bot.command()
async def leaderboard(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT r.mxb_name, r.class_name, COALESCE(SUM(res.points), 0) AS pts
                    FROM riders r
                    LEFT JOIN results res ON r.id = res.rider_id
                    GROUP BY r.id, r.mxb_name, r.class_name
                    ORDER BY pts DESC, r.mxb_name ASC
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No leaderboard data yet.")
            return

        msg = "\n".join([
            f"{i+1}. {r['mxb_name']} | {r['class_name']} | {r['pts']} pts"
            for i, r in enumerate(rows[:20])
        ])
        await ctx.send("🏆 Leaderboard\n" + msg)
    except Exception as e:
        await ctx.send(f"❌ leaderboard failed: {e}")


@bot.command()
async def advance(ctx, event_id: int):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT race_stage FROM events WHERE id = %s", (event_id,))
                event = cur.fetchone()

                if not event:
                    await ctx.send("❌ Event not found")
                    return

                current = event["race_stage"]
                if current == "qualifying":
                    new_stage = "heat1"
                elif current == "heat1":
                    new_stage = "heat2"
                elif current == "heat2":
                    new_stage = "final"
                else:
                    new_stage = "final"

                cur.execute("""
                    UPDATE events
                    SET race_stage = %s
                    WHERE id = %s
                """, (new_stage, event_id))
                conn.commit()

        await ctx.send(f"✅ Event #{event_id} moved to {new_stage}")
    except Exception as e:
        await ctx.send(f"❌ advance failed: {e}")


bot.run(TOKEN)
