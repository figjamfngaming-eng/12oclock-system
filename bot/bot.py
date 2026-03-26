import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is missing")
if not GUILD_ID:
    raise RuntimeError("DISCORD_GUILD_ID is missing")

GUILD_OBJ = discord.Object(id=int(GUILD_ID))

intents = discord.Intents.default()

class MyBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Clear old guild commands so Discord stops using stale ones
        self.tree.clear_commands(guild=GUILD_OBJ)

        @self.tree.command(
            name="ping",
            description="Check if the bot is alive",
            guild=GUILD_OBJ,
        )
        async def ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message("🏓 Pong! Bot is working")

        synced = await self.tree.sync(guild=GUILD_OBJ)
        print(f"Synced {len(synced)} guild command(s) to guild {GUILD_ID}")

bot = MyBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready() -> None:
    print(f"Bot ready as {bot.user} ({bot.user.id})")

bot.run(TOKEN)
