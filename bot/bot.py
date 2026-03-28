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

ROLE_1W_MXGP_450_ID = int(os.getenv("ROLE_1W_MXGP_450_ID", "0"))
ROLE_1W_MXGP_250F_ID = int(os.getenv("ROLE_1W_MXGP_250F_ID", "0"))
ROLE_1W_MXGP_125_ID = int(os.getenv("ROLE_1W_MXGP_125_ID", "0"))
ROLE_1W_MXGP_85_ID = int(os.getenv("ROLE_1W_MXGP_85_ID", "0"))
ROLE_1W_SMX_450_ID = int(os.getenv("ROLE_1W_SMX_450_ID", "0"))
ROLE_1W_SMX_250F_ID = int(os.getenv("ROLE_1W_SMX_250F_ID", "0"))
ROLE_1W_SMX_125_ID = int(os.getenv("ROLE_1W_SMX_125_ID", "0"))
ROLE_1W_SMX_85_ID = int(os.getenv("ROLE_1W_SMX_85_ID", "0"))

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
    value = (class_name or "").upper().strip()
    if value == "SM85":
        return ROLE_SM85_ID
    if value == "SM125":
        return ROLE_SM125_ID
    if value == "250F":
        return ROLE_250F_ID
    if value == "450":
        return ROLE_450_ID
    return 0


def one_w_role_id(series: str, class_name: str) -> int:
    s = (series or "").upper().strip()
    c = (class_name or "").upper().strip()

    if s == "MXGP":
        if c == "450":
            return ROLE_1W_MXGP_450_ID
        if c == "250F":
            return ROLE_1W_MXGP_250F_ID
        if c == "SM125" or c == "125":
            return ROLE_1W_MXGP_125_ID
        if c == "SM85" or c == "85":
            return ROLE_1W_MXGP_85_ID

    if s == "SMX":
        if c == "450":
            return ROLE_1W_SMX_450_ID
        if c == "250F":
            return ROLE_1W_SMX_250F_ID
        if c == "SM125" or c == "125":
            return ROLE_1W_SMX_125_ID
        if c == "SM85" or c == "85":
            return ROLE_1W_SMX_85_ID

    return 0


async def get_guild():
    guild = bot.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        guild = await bot.fetch_guild(DISCORD_GUILD_ID)
    return guild


async def sync_member_roles_for_discord_user(discord_user_id: str):
    guild = await get_guild()
    member = guild.get_member(int(discord_user_id))
    if member is None:
        member = await guild.fetch_member(int(discord_user_id))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    al.discord_id,
                    al.discord_username,
                    al.link_status,
                    al.approved AS link_approved,
                    r.id AS rider_id,
                    r.class_name,
                    r.approved AS rider_approved
                FROM account_links al
                LEFT JOIN riders r
                    ON r.discord_user_id = al.discord_id
                    OR r.discord_id = al.discord_id
                WHERE al.discord_id = %s
                LIMIT 1
            """, (discord_user_id,))
            row = cur.fetchone()

    if not row:
        return False, "No linked account found."

    if not row["link_approved"]:
        return False, "Link is not approved yet."

    linked_role = guild.get_role(ROLE_LINKED_ID) if ROLE_LINKED_ID else None
    class_role = guild.get_role(class_role_id(row["class_name"])) if class_role_id(row["class_name"]) else None

    roles_to_add = []
    roles_to_remove = []

    if linked_role and linked_role not in member.roles:
        roles_to_add.append(linked_role)

    class_role_ids = {rid for rid in [ROLE_SM85_ID, ROLE_SM125_ID, ROLE_250F_ID, ROLE_450_ID] if rid}
    for role in member.roles:
        if role.id in class_role_ids and (not class_role or role.id != class_role.id):
            roles_to_remove.append(role)

    if class_role and class_role not in member.roles:
        roles_to_add.append(class_role)

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Updating class role sync")

    if roles_to_add:
        await member.add_roles(*roles_to_add, reason="Approved website account link")

    return True, "Roles synced."


async def update_one_w_role_for_series_class(series: str, class_name: str):
    role_id = one_w_role_id(series, class_name)
    if not role_id:
        return False, f"No 1W role configured for {series} {class_name}"

    guild = await get_guild()
    target_role = guild.get_role(role_id)
    if not target_role:
        return False, f"Discord role not found for {series} {class_name}"

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    r.discord_user_id,
                    r.discord_id,
                    r.mxb_name,
                    COALESCE(SUM(res.points), 0) AS total_points
                FROM results res
                JOIN riders r ON r.id = res.rider_id
                JOIN events e ON e.id = res.event_id
                WHERE UPPER(COALESCE(e.series, 'MXGP')) = UPPER(%s)
                  AND UPPER(e.class_name) = UPPER(%s)
                GROUP BY r.id, r.discord_user_id, r.discord_id, r.mxb_name
                ORDER BY total_points DESC, r.mxb_name ASC
                LIMIT 1
            """, (series, class_name))
            leader = cur.fetchone()

    if not leader:
        return False, f"No points found for {series} {class_name}"

    target_discord_id = leader["discord_user_id"] or leader["discord_id"]
    if not target_discord_id:
        return False, "Leader has no Discord ID saved"

    member = guild.get_member(int(target_discord_id))
    if member is None:
        member = await guild.fetch_member(int(target_discord_id))

    members_with_role = list(target_role.members)
    to_remove = [m for m in members_with_role if m.id != member.id]

    if to_remove:
        for m in to_remove:
            await m.remove_roles(target_role, reason=f"1W transferred for {series} {class_name}")

    if target_role not in member.roles:
        await member.add_roles(target_role, reason=f"Current points leader for {series} {class_name}")

    return True, f"1W updated: {leader['mxb_name']} now holds {series} {class_name}"


@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong! Bot is working")


@bot.command()
async def register(ctx, name: str, guid: str, cls: str):
    try:
        cls = cls.upper().strip()

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO riders (
                        discord_id,
                        discord_user_id,
                        discord_username,
                        mxb_name,
                        guid,
                        class_name,
                        approved,
                        is_linked
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE, FALSE)
                    ON CONFLICT (discord_id) DO UPDATE SET
                        discord_user_id = EXCLUDED.discord_user_id,
                        discord_username = EXCLUDED.discord_username,
                        mxb_name = EXCLUDED.mxb_name,
                        guid = EXCLUDED.guid,
                        class_name = EXCLUDED.class_name
                """, (
                    str(ctx.author.id),
                    str(ctx.author.id),
                    str(ctx.author),
                    name,
                    guid,
                    cls
                ))
                conn.commit()

        await ctx.send(f"✅ Registered {name} in class {cls}")
    except Exception as e:
        await ctx.send(f"❌ register failed: {e}")


@bot.command()
async def riders(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, mxb_name, class_name, approved
                    FROM riders
                    ORDER BY id DESC
                    LIMIT 20
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No riders found.")
            return

        msg = "\n".join([
            f"#{r['id']} - {r['mxb_name']} | {r['class_name']} | approved={r['approved']}"
            for r in rows
        ])
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ riders failed: {e}")


@bot.command()
async def create_event(ctx, *, args: str):
    try:
        parts = args.split("|")
        if len(parts) != 3:
            await ctx.send("Usage: !create_event Event Name | MXGP | 450")
            return

        name = parts[0].strip()
        series = parts[1].strip().upper()
        cls = parts[2].strip().upper()

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO events (name, series, class_name, race_stage)
                    VALUES (%s, %s, %s, 'qualifying')
                """, (name, series, cls))
                conn.commit()

        await ctx.send(f"✅ Event created: {name} | {series} | {cls}")
    except Exception as e:
        await ctx.send(f"❌ create_event failed: {e}")


@bot.command()
async def events(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage
                    FROM events
                    ORDER BY id DESC
                    LIMIT 20
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No events found.")
            return

        msg = "\n".join([
            f"#{r['id']} - {r['name']} | {r['series']} | {r['class_name']} | {r['race_stage']}"
            for r in rows
        ])
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ events failed: {e}")


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
                    await ctx.send("❌ Your account is not linked and approved yet.")
                    return

                cur.execute("""
                    SELECT id, class_name
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
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO results (event_id, rider_id, position, points)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id, rider_id) DO UPDATE SET
                        position = EXCLUDED.position,
                        points = EXCLUDED.points
                """, (event_id, rider_id, position, pts))

                cur.execute("""
                    SELECT COALESCE(series, 'MXGP') AS series, class_name
                    FROM events
                    WHERE id = %s
                """, (event_id,))
                event = cur.fetchone()

                conn.commit()

        if event:
            ok, msg = await update_one_w_role_for_series_class(event["series"], event["class_name"])
            if ok:
                await ctx.send(f"✅ Result saved | Rider #{rider_id} | Pos {position} | Points {pts}\n🏆 {msg}")
            else:
                await ctx.send(f"✅ Result saved | Rider #{rider_id} | Pos {position} | Points {pts}\n⚠️ {msg}")
        else:
            await ctx.send(f"✅ Result saved | Rider #{rider_id} | Pos {position} | Points {pts}")

    except Exception as e:
        await ctx.send(f"❌ result failed: {e}")


@bot.command()
async def leaderboard(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        r.mxb_name,
                        r.class_name,
                        COALESCE(SUM(res.points), 0) AS pts
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
async def update_1w(ctx, series: str, cls: str):
    try:
        series = series.upper().strip()
        cls = cls.upper().strip()
        ok, msg = await update_one_w_role_for_series_class(series, cls)
        if ok:
            await ctx.send(f"✅ {msg}")
        else:
            await ctx.send(f"❌ {msg}")
    except Exception as e:
        await ctx.send(f"❌ update_1w failed: {e}")


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
                    SET approved = TRUE,
                        link_status = 'approved'
                    WHERE discord_id = %s
                """, (discord_user_id,))

                cur.execute("""
                    UPDATE riders
                    SET approved = TRUE,
                        is_linked = TRUE,
                        discord_user_id = %s
                    WHERE discord_id = %s
                       OR discord_user_id = %s
                """, (discord_user_id, discord_user_id, discord_user_id))
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


bot.run(TOKEN)
