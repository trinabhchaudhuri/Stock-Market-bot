# Coinflip Bot

Discord bot for economy, stocks, blackjack, and chart generation.

## Setup

1. Create and activate a Python virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set your credentials:
   ```powershell
   copy .env.example .env
   ```
4. Run the bot:
   ```powershell
   python bot.py
   ```

## Environment variables

The bot uses the following variables in `.env`:

- `DISCORD_TOKEN`
- `CLIENT_ID`
- `TOPGG_BOT_ID` (optional, falls back to `CLIENT_ID` if unset)

## Requirements

- `discord.py`
- `python-dotenv`
- `matplotlib`

## Notes

- `economy.json` and `stocks.json` are created automatically if missing.
- Keep your bot token private and never commit `.env` to version control.
