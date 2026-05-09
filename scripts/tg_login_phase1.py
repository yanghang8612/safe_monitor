"""Phase 1: request a login code from Telegram.

The code arrives in the user's Telegram app (typically in the "Telegram" official
chat) within a few seconds. Save the `phone_code_hash` for phase 2.
"""

import asyncio
import os

from telethon import TelegramClient


_HASH_FILE = "/app/data/.tg_code_hash" if os.path.isdir("/app/data") else "./data/.tg_code_hash"


async def main() -> None:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    phone = os.environ["TG_USERBOT_PHONE"]
    session = os.environ.get("TG_USERBOT_SESSION", "./data/userbot.session")

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    # Persist phone_code_hash inside the data/ volume so phase 2 (running in
    # a separate ephemeral container with `docker compose run --rm`) can read it.
    os.makedirs(os.path.dirname(_HASH_FILE), exist_ok=True)
    with open(_HASH_FILE, "w") as f:
        f.write(sent.phone_code_hash)
    print(f"OK code_requested to {phone}; session={session}; hash_file={_HASH_FILE}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
