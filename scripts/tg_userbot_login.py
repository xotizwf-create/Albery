"""Two-step interactive login of the manager account's MTProto session (run on the box).

  step 1:  .venv/bin/python scripts/tg_userbot_login.py request +79991234567
           -> Telegram sends a login code AND prints WHERE it went (приложение / SMS / звонок).
  step 1b: .venv/bin/python scripts/tg_userbot_login.py resend
           -> перевыслать код следующим каналом (обычно SMS), если в приложении его нет.
  step 2:  .venv/bin/python scripts/tg_userbot_login.py confirm 12345 [cloud-password]
           -> signs in, creates .tg_userbot.session (chmod 600), removes the temp file.

  без кода: .venv/bin/python scripts/tg_userbot_login.py qr [минут]
           -> вход привязкой устройства по QR (Настройки → Устройства → Подключить устройство).
              Текущая ссылка лежит в .tg_qr_url, по ней рисуется QR владельцу.

The code expires in a few minutes — run step 2 promptly. The cloud password argument is
needed only when двухэтапная аутентификация включена on the account.

Куда Telegram шлёт код, решает он сам: если у аккаунта есть живая сессия на телефоне или в
десктопе — код придёт СООБЩЕНИЕМ В САМ TELEGRAM (служебный чат «Telegram»), и только если
таких сессий нет — по SMS. Поэтому канал доставки печатается: без него человек ищет код не
там (так и вышло 05.08.2026 — скрипт печатал заглушку «отправлен в Telegram аккаунта»
независимо от того, что ответил сервер).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tg_userbot  # noqa: E402

PENDING = tg_userbot.APP_ROOT / ".tg_userbot_login.json"
QR_URL = tg_userbot.APP_ROOT / ".tg_qr_url"

_CHANNELS = {
    "SentCodeTypeApp": "сообщением В САМОМ TELEGRAM (служебный чат «Telegram» на устройстве, "
                       "где аккаунт уже залогинен)",
    "SentCodeTypeSms": "по SMS на номер",
    "SentCodeTypeCall": "голосовым звонком с диктовкой кода",
    "SentCodeTypeFlashCall": "сбросом звонка (код — в номере звонящего)",
    "SentCodeTypeMissedCall": "пропущенным звонком (код — последние цифры номера)",
    "SentCodeTypeEmail": "письмом на привязанную почту",
    "SentCodeTypeSetUpEmailRequired": "требуется привязать почту — вход из консоли невозможен",
}


def _describe(sent) -> str:
    kind = type(sent.type).__name__
    where = _CHANNELS.get(kind, kind)
    length = getattr(sent.type, "length", None)
    nxt = type(sent.next_type).__name__ if getattr(sent, "next_type", None) else None
    out = f"Код отправлен {where}"
    if length:
        out += f", {length} цифр"
    if nxt:
        out += f". Если его там нет — «resend» перевышлет {_CHANNELS.get(nxt, nxt)}"
    return out + "."


def _remember(phone: str, sent) -> None:
    PENDING.write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash}),
                       encoding="utf-8")
    os.chmod(PENDING, 0o600)


async def request(phone: str) -> None:
    client = tg_userbot._client()
    await client.connect()
    sent = await client.send_code_request(phone)
    _remember(phone, sent)
    await client.disconnect()
    print(_describe(sent))
    print("Дальше: confirm <код> [пароль-2FA]")


async def resend() -> None:
    """Перевыслать код следующим каналом — тем, который сервер назвал next_type."""
    from telethon.tl.functions.auth import ResendCodeRequest

    pend = json.loads(PENDING.read_text(encoding="utf-8"))
    client = tg_userbot._client()
    await client.connect()
    sent = await client(ResendCodeRequest(pend["phone"], pend["hash"]))
    _remember(pend["phone"], sent)
    await client.disconnect()
    print(_describe(sent))
    print("Дальше: confirm <код> [пароль-2FA]")


async def qr(minutes: int = 15) -> None:
    """Вход привязкой устройства: Telegram показывает QR, владелец сканирует его телефоном
    (Настройки → Устройства → Подключить устройство). Кода при этом не нужно вообще — путь на
    случай, когда Telegram перестал слать коды (05.08.2026: после пары запросов подряд он молча
    прекращает доставку в служебный чат, и войти по коду нельзя).

    Токен живёт около половины минуты, поэтому ссылка периодически пересоздаётся; каждая новая
    пишется в .tg_qr_url, откуда её забирает тот, кто рисует QR владельцу.
    """
    from telethon.errors import SessionPasswordNeededError

    client = tg_userbot._client()
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        QR_URL.write_text(f"AUTHORIZED @{me.username}", encoding="utf-8")
        await client.disconnect()
        print(f"Уже авторизован: @{me.username}")
        return
    qr_login = await client.qr_login()
    deadline = time.time() + minutes * 60
    try:
        while time.time() < deadline:
            # Рядом со ссылкой пишем момент её протухания: тот, кто рисует QR, обязан знать,
            # сколько времени у владельца осталось. Токен пересоздаётся ТОЛЬКО перед самым
            # истечением — иначе показанный код успевает устареть раньше, чем его отсканируют,
            # и Telegram отвечает «неверный QR» (напоролись 05.08.2026).
            QR_URL.write_text(f"{qr_login.url}|{int(qr_login.expires.timestamp())}",
                              encoding="utf-8")
            os.chmod(QR_URL, 0o600)
            try:
                await qr_login.wait(10)
                break
            except asyncio.TimeoutError:
                if qr_login.expires.timestamp() - time.time() < 12:
                    await qr_login.recreate()
        else:
            QR_URL.write_text("EXPIRED", encoding="utf-8")
            print("Время ожидания вышло — никто не отсканировал QR.")
            await client.disconnect()
            return
    except SessionPasswordNeededError:
        QR_URL.write_text("PASSWORD-NEEDED", encoding="utf-8")
        print("На аккаунте облачный пароль (2FA) — вход по QR требует его отдельно.")
        await client.disconnect()
        return
    me = await client.get_me()
    await client.disconnect()
    QR_URL.write_text(f"AUTHORIZED @{me.username}", encoding="utf-8")
    PENDING.unlink(missing_ok=True)
    tg_userbot._secure_session()
    print(f"Сессия создана: @{me.username} (id {me.id}). Файл {tg_userbot.SESSION_FILE} (600).")


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
    elif len(sys.argv) >= 2 and sys.argv[1] == "resend":
        asyncio.run(resend())
    elif len(sys.argv) >= 2 and sys.argv[1] == "qr":
        asyncio.run(qr(int(sys.argv[2]) if len(sys.argv) > 2 else 15))
    elif len(sys.argv) >= 3 and sys.argv[1] == "confirm":
        asyncio.run(confirm(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        print(__doc__)
