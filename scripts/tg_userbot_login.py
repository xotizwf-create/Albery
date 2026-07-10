"""Two-step interactive login of the manager account's MTProto session (run on the box).

  step 1:  .venv/bin/python scripts/tg_userbot_login.py request +79991234567
           -> Telegram sends a login code to the owner's app; the hash is kept in a temp file.
  step 2:  .venv/bin/python scripts/tg_userbot_login.py confirm 12345 [cloud-password]
           -> signs in, creates .tg_userbot.session (chmod 600), removes the temp file.

The code expires in a few minutes — run step 2 promptly. The cloud password argument is
needed only when двухэтапная аутентификация включена on the account.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tg_userbot  # noqa: E402

PENDING = tg_userbot.APP_ROOT / ".tg_userbot_login.json"


async def request(phone: str) -> None:
    client = tg_userbot._client()
    await client.connect()
    sent = await client.send_code_request(phone)
    PENDING.write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash}),
                       encoding="utf-8")
    os.chmod(PENDING, 0o600)
    await client.disconnect()
    print("Код отправлен в Telegram аккаунта. Теперь: confirm <код> [пароль-2FA]")


async def confirm(code: str, password: str | None) -> None:
    from telethon.errors import SessionPasswordNeededError
    pend = json.loads(PENDING.read_text(encoding="utf-8"))
    client = tg_userbot._client()
    await client.connect()
    try:
        await client.sign_in(phone=pend["phone"], code=code, phone_code_hash=pend["hash"])
    except SessionPasswordNeededError:
        if not password:
            print("На аккаунте включён облачный пароль (2FA) — повторите: confirm <код> <пароль>")
            await client.disconnect()
            return
        await client.sign_in(password=password)
    me = await client.get_me()
    await client.disconnect()
    PENDING.unlink(missing_ok=True)
    tg_userbot._secure_session()
    print(f"Сессия создана: @{me.username} (id {me.id}). Файл {tg_userbot.SESSION_FILE} (600).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "request":
        asyncio.run(request(sys.argv[2]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "confirm":
        asyncio.run(confirm(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        print(__doc__)
