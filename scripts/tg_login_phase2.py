"""Phase 2: submit the login code received in Telegram.

Reads phone_code_hash from data/.tg_code_hash (written by phase 1; persisted
inside the data/ volume so it survives between `docker compose run --rm` invocations).
Usage: TG_LOGIN_CODE=12345 python scripts/tg_login_phase2.py
"""

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

_HASH_FILE = "/app/data/.tg_code_hash" if os.path.isdir("/app/data") else "./data/.tg_code_hash"


async def main() -> None:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    phone = os.environ["TG_USERBOT_PHONE"]
    session = os.environ.get("TG_USERBOT_SESSION", "./data/userbot.session")
    code = os.environ.get("TG_LOGIN_CODE") or (sys.argv[1] if len(sys.argv) > 1 else None)
    password = os.environ.get("TG_2FA_PASSWORD")  # optional
    if not code:
        print("ERROR: pass code via TG_LOGIN_CODE env var or argv[1]")
        sys.exit(1)

    with open(_HASH_FILE) as f:
        phone_code_hash = f.read().strip()

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    try:
        me = await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not password:
            print("ERROR: 2FA enabled; set TG_2FA_PASSWORD env var and re-run")
            sys.exit(2)
        me = await client.sign_in(password=password)
    print(f"OK logged_in user_id={me.id} session={session}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
