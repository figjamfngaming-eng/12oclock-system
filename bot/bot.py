import sys
import os

# Fix import path so /shared can be used when running from /bot

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(**file**), "..")))

import discord
from discord.ext import commands
from discord import app_commands

from shared.db import init_db, q, exec_sql

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- BOT READY ----------

@bot.event
async def on_ready():
print(f"Logged in as {bot.user}")

```
try:
    synced = await bot.tree.sync()
    print(f"Slash commands synced: {len(synced)}")
except Exception as e:
    print(e)

init_db()
```

# ---------- PING COMMAND ----------

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
await interaction.response.send_message("🏁 Pong! Bot is running.")

# ---------- CREATE EVENT ----------

@bot.tree.command(name="create_event", description="Create a race event")
@app_commands.describe(
name="Event name",
track="Track name",
race_class="Class (250 / 450 / Open)"
)
async def create_event(interaction: discord.Interaction, name: str, track: str, race_class: str):

```
exec_sql(
    "INSERT INTO events (name, track, class) VALUES (?, ?, ?)",
    (name, track, race_class)
)

await interaction.response.send_message(
    f"🏁 Event created\n\n"
    f"**Name:** {name}\n"
    f"**Track:** {track}\n"
    f"**Class:** {race_class}"
)
```

# ---------- EVENTS LIST ----------

@bot.tree.command(name="events", description="List all events")
async def events(interaction: discord.Interaction):

```
events = q("SELECT id, name, track, class FROM events ORDER BY id DESC")

if not events:
    await interaction.response.send_message("No events created yet.")
    return

msg = "🏁 **Race Events**\n\n"

for e in events:
    msg += f"ID {e[0]} | {e[1]} | {e[2]} | {e[3]}\n"

await interaction.response.send_message(msg)
```

# ---------- STANDINGS ----------

@bot.tree.command(name="standings", description="Show championship standings")
@app_commands.describe(race_class="Class (250 / 450 / Open)")
async def standings(interaction: discord.Interaction, race_class: str):

```
rows = q(
    "SELECT rider_name, points FROM standings WHERE class=? ORDER BY points DESC",
    (race_class,)
)

if not rows:
    await interaction.response.send_message("No standings yet.")
    return

msg = f"🏆 **{race_class} Standings**\n\n"

pos = 1
for r in rows:
    msg += f"{pos}. {r[0]} — {r[1]} pts\n"
    pos += 1

await interaction.response.send_message(msg)
```

# ---------- PENALTY ----------

@bot.tree.command(name="penalty", description="Apply penalty points")
@app_commands.describe(
rider="Rider name",
points="Points to remove"
)
async def penalty(interaction: discord.Interaction, rider: str, points: int):

```
exec_sql(
    "UPDATE standings SET points = points - ? WHERE rider_name=?",
    (points, rider)
)

await interaction.response.send_message(
    f"⚠️ Penalty applied\n\n"
    f"{points} points removed from **{rider}**"
)
```

# ---------- RUN BOT ----------

bot.run(TOKEN)
