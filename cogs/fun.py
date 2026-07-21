"""Fun commands: coinflip, roll, choose, 8ball."""

import random

from discord import app_commands
from discord.ext import commands

EIGHT_BALL_ANSWERS = [
    "It is certain.",
    "Without a doubt.",
    "Yes, definitely.",
    "Most likely.",
    "Signs point to yes.",
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Don't count on it.",
    "My reply is no.",
    "Outlook not so good.",
    "Very doubtful.",
]


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(description="Flip a coin.")
    async def coinflip(self, ctx: commands.Context):
        await ctx.send(f"🪙 {random.choice(['Heads', 'Tails'])}!")

    @commands.hybrid_command(description="Roll dice, e.g. 2d6.")
    @app_commands.describe(dice="Dice in NdS format, like 1d20 or 2d6.")
    async def roll(self, ctx: commands.Context, dice: str = "1d6"):
        try:
            count_str, sides_str = dice.lower().split("d")
            count, sides = int(count_str or 1), int(sides_str)
            if not (1 <= count <= 100 and 2 <= sides <= 1000):
                raise ValueError
        except ValueError:
            await ctx.send("Use NdS format, e.g. `1d20` or `2d6` (max 100 dice, 1000 sides).")
            return
        rolls = [random.randint(1, sides) for _ in range(count)]
        detail = " + ".join(map(str, rolls)) if count > 1 else str(rolls[0])
        await ctx.send(f"🎲 `{dice}` → **{sum(rolls)}** ({detail})")

    @commands.hybrid_command(description="Choose between options, separated by commas.")
    @app_commands.describe(options="Comma-separated options, e.g. pizza, sushi, tacos")
    async def choose(self, ctx: commands.Context, *, options: str):
        choices = [c.strip() for c in options.split(",") if c.strip()]
        if len(choices) < 2:
            await ctx.send("Give me at least two comma-separated options.")
            return
        await ctx.send(f"I choose **{random.choice(choices)}**!")

    @commands.hybrid_command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="Your yes/no question.")
    async def eight_ball(self, ctx: commands.Context, *, question: str):
        await ctx.send(f"🎱 {random.choice(EIGHT_BALL_ANSWERS)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
