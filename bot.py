"""AKEMM FSVO BOT — entry point."""

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("akemm")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s (id %s)", bot.user, bot.user.id)
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))
    except discord.HTTPException:
        log.exception("Failed to sync slash commands")


async def load_cogs():
    cogs_dir = Path(__file__).parent / "cogs"
    for file in sorted(cogs_dir.glob("*.py")):
        if file.stem.startswith("_"):
            continue
        ext = f"cogs.{file.stem}"
        await bot.load_extension(ext)
        log.info("Loaded extension %s", ext)


async def main():
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and add your token."
        )
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
