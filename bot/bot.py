import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_OBJ = discord.Object(id=GUILD_ID)

class MyBot(commands.Bot):
    async def setup_hook(self):
        # Clear old guild commands first
        self.tree.clear_commands(guild=GUILD_OBJ)

        # Add fresh guild command
        @self.tree.command(name="ping", description="Check bot is alive", guild=GUILD_OBJ)
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("🏓 Pong! Bot is working")

        # Sync fresh guild commands
        synced = await self.tree.sync(guild=GUILD_OBJ)
        print(f"Synced {len(synced)} guild command(s)")

bot = MyBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user}")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN missing")
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID missing")

bot.run(TOKEN)
