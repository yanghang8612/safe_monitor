"""
One-time interactive login for telethon userbot.

Run LOCALLY (not in Docker) so you can type the SMS/Telegram code:

  uv run python scripts/telegram_login.py

After success, copy the generated session file to your server:
  scp data/userbot.session server:/path/to/safe_monitor/data/
"""

import asyncio
import os

from telethon import TelegramClient


async def main() -> None:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    phone = os.environ["TG_USERBOT_PHONE"]
    session = os.environ.get("TG_USERBOT_SESSION", "./data/userbot.session")

    client = TelegramClient(session, api_id, api_hash)
    await client.start(phone=phone)
    print(f"Logged in. Session saved at: {session}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
