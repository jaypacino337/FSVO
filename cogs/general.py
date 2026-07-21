"""General-purpose commands: ping, info, serverinfo, avatar."""

import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(description="Check the bot's latency.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong! `{round(self.bot.latency * 1000)}ms`")

    @commands.hybrid_command(description="About this bot.")
    async def info(self, ctx: commands.Context):
        embed = discord.Embed(
            title="AKEMM FSVO BOT",
            description="A modular Discord bot built with discord.py.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)))
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms")
        await ctx.send(embed=embed)

    @commands.hybrid_command(description="Show information about this server.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.green())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Members", value=str(guild.member_count))
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "D"))
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
        await ctx.send(embed=embed)

    @commands.hybrid_command(description="Show a user's avatar.")
    @app_commands.describe(user="The user whose avatar to show (defaults to you).")
    async def avatar(self, ctx: commands.Context, user: discord.User | None = None):
        user = user or ctx.author
        embed = discord.Embed(title=f"{user.display_name}'s avatar")
        embed.set_image(url=user.display_avatar.url)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
