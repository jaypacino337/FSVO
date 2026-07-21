# AKEMM FSVO BOT

AKEMM is a Discord bot built with [discord.py](https://discordpy.readthedocs.io/). It ships with a small set of general-purpose and fun commands and a modular "cog" layout that makes it easy to add your own.

## Features

- Slash commands and classic `!` prefix commands
- **General**: `/ping`, `/info`, `/serverinfo`, `/avatar`
- **Fun**: `/coinflip`, `/roll`, `/choose`, `/8ball`
- Modular cogs — drop a new file into `cogs/` and it loads automatically
- Configuration via environment variables (no tokens in code)

## Requirements

- Python 3.10+
- A Discord bot token

## Setup

1. **Create a Discord application**
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
   - Under **Bot**, click **Reset Token** and copy the token.
   - Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent** (needed for prefix commands).

2. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure the token**

   ```bash
   cp .env.example .env
   # then edit .env and paste your token
   ```

4. **Run the bot**

   ```bash
   python bot.py
   ```

5. **Invite it to your server**
   - In the Developer Portal, go to **OAuth2 → URL Generator**.
   - Scopes: `bot`, `applications.commands`. Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`.
   - Open the generated URL and add the bot to your server.

## Project layout

```
bot.py            # entry point: loads cogs, syncs slash commands
cogs/
  general.py      # ping, info, serverinfo, avatar
  fun.py          # coinflip, roll, choose, 8ball
requirements.txt
.env.example      # template for your bot token
```

## Adding commands

Create a new file in `cogs/` following the pattern in `cogs/fun.py` — any file with a `setup()` function is loaded automatically at startup.
