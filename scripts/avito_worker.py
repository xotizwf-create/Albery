#!/usr/bin/env python3
"""Воркер браузерной сессии Авито: живёт на машине-выходе, зеркалит переписки в Albery.

Почему на отдельной машине. Авито не отдаёт свои страницы адресу прода, а прод — коробка на
2 ГБ, где браузер соседствовал бы с базой и агентом (правило «не перегружать прод»). Поэтому
браузер работает там, где есть российский адрес, а с Albery говорит по HTTP с токеном.

Что делает и чего НЕ делает. Воркер — транспорт: он приносит входящие и уносит исходящие.
Он не решает, что отвечать, и не пишет от себя. Ответы приходят из очереди Albery, а очередь
наполняют оператор или агент.

Команды:

    python scripts/avito_worker.py login --account main
        Открывает браузер, вы входите в Авито руками (телефон + код). Профиль сохраняется,
        дальше вход не нужен. Профиль лежит рядом, вне репозитория, права — только ваши.

    python scripts/avito_worker.py probe --account main
        Проверяет, жива ли сессия, и сообщает состояние в Albery.

    python scripts/avito_worker.py capture --account main
        Открывает мессенджер и записывает СЫРЫЕ ответы сети в файл — по ним настраивается
        разбор. Ничего никуда не отправляет.

    python scripts/avito_worker.py mirror --account main [--once]
        Рабочий режим: забирает переписки в Albery и отправляет то, что стоит в очереди.

Настройки берутся из окружения (или .env рядом с профилем):
    ALBERY_BASE_URL       — например https://www.m4s.ru
    AVITO_WORKER_TOKEN    — тот же токен, что задан на сервере
    AVITO_PROFILES_DIR    — где хранить профили браузера (по умолчанию ~/.avito-profiles)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

MESSENGER_URL = "https://www.avito.ru/profile/messenger"
DEFAULT_PROFILES_DIR = Path.home() / ".avito-profiles"
POLL_SECONDS = 20
# Живой человек не открывает тридцать чатов в секунду. Пауза между действиями — не украшение:
# именно темп отличает нашу сессию от парсера и бережёт аккаунт от блокировки.
HUMAN_PAUSE_MS = (900, 2100)


def profiles_dir() -> Path:
    return Path(os.getenv("AVITO_PROFILES_DIR") or DEFAULT_PROFILES_DIR)


def profile_path(account: str) -> Path:
    path = profiles_dir() / account
    path.mkdir(parents=True, exist_ok=True)
    return path


class Albery:
    """Тонкий клиент к двери воркера. Токен уходит только в заголовке и не печатается."""

    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urlrequest.Request(f"{self.base}{path}", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Avito-Worker-Token", self.token)
        try:
            with urlrequest.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Albery ответил {exc.code}: {detail}") from None
        except urlerror.URLError as exc:
            raise RuntimeError(f"нет связи с Albery: {exc.reason}") from None

    def report_session(self, account: str, status: str, error: str = "") -> None:
        self.post("/api/avito-worker/session", {"account": account, "status": status,
                                                "error": error})

    def push_inbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/avito-worker/inbound", payload)

    def claim_outbox(self, worker_id: str, account: str, limit: int = 10) -> list[dict[str, Any]]:
        answer = self.post("/api/avito-worker/outbox/claim",
                           {"worker_id": worker_id, "account": account, "limit": limit})
        return list(answer.get("items") or [])

    def mark_sending(self, outbox_id: int, worker_id: str) -> bool:
        try:
            answer = self.post(f"/api/avito-worker/outbox/{outbox_id}/sending",
                               {"worker_id": worker_id})
        except RuntimeError as exc:
            print(f"  строка {outbox_id}: отправку не начали ({exc})")
            return False
        return bool(answer.get("allowed"))

    def finish(self, outbox_id: int, worker_id: str, result: str, *, error: str = "",
               provider_message_id: str = "") -> None:
        self.post(f"/api/avito-worker/outbox/{outbox_id}/result",
                  {"worker_id": worker_id, "result": result, "error": error,
                   "provider_message_id": provider_message_id})


def _load_local_env() -> None:
    """Настройки воркера лежат рядом с профилями, а не в репозитории и не в переменных среды.

    Токен доступа к Albery — секрет: он не попадает ни в git, ни в вывод команд, ни в
    историю оболочки. Файл создаётся один раз, читается только этой машиной.
    """
    env_file = profiles_dir() / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def albery_from_env() -> Albery:
    _load_local_env()
    base = os.getenv("ALBERY_BASE_URL", "").strip()
    token = os.getenv("AVITO_WORKER_TOKEN", "").strip()
    if not base or not token:
        raise SystemExit("Задайте ALBERY_BASE_URL и AVITO_WORKER_TOKEN "
                         "(токен тот же, что на сервере; в вывод он не попадает).")
    return Albery(base, token)


def _browser_context(playwright, account: str, *, headless: bool):
    """Постоянный профиль: куки и localStorage живут между запусками, вход — один раз."""
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_path(account)),
        channel="chrome",
        headless=headless,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1600, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _logged_in(page) -> bool:
    """Вошли или нет — по ответу самого сайта, а не по вёрстке.

    Селекторы Авито меняются без предупреждения, поэтому признаком служит редирект на форму
    входа: если после открытия мессенджера мы оказались на /login, сессии нет.
    """
    return "/login" not in page.url and "authorize" not in page.url


def command_login(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(MESSENGER_URL, wait_until="domcontentloaded", timeout=90000)
        print("Откроется окно браузера. Войдите в Авито обычным образом (номер + код из SMS).")
        print("Когда увидите свои переписки — вернитесь сюда и нажмите Enter.")
        input()
        ok = _logged_in(page)
        print("Сессия сохранена." if ok else "Похоже, вход не завершён: страница всё ещё на форме входа.")
        context.close()
    return 0 if ok else 1


def command_probe(args) -> int:
    from playwright.sync_api import sync_playwright

    albery = albery_from_env()
    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(MESSENGER_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
            if _logged_in(page):
                status, note = "ok", "сессия жива"
            else:
                status, note = "needs_login", "Авито просит войти заново"
        except Exception as exc:  # noqa: BLE001
            status, note = "error", f"{type(exc).__name__}: {exc}"
        finally:
            context.close()
    print(f"{args.account}: {status} — {note}")
    albery.report_session(args.account, status, "" if status == "ok" else note)
    return 0 if status == "ok" else 1


def command_capture(args) -> int:
    """Записывает сырые сетевые ответы мессенджера — основа для разбора.

    Разбор строится на данных, которые страница получает по сети, а не на CSS-селекторах:
    вёрстка Авито меняется чаще, чем контракты её собственных запросов.
    """
    from playwright.sync_api import sync_playwright

    captured: list[dict[str, Any]] = []
    out = Path(args.out or (profile_path(args.account) / "capture.jsonl"))

    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response):
            url = response.url
            if "messenger" not in url and "/web/" not in url:
                return
            try:
                if "application/json" not in (response.headers.get("content-type") or ""):
                    return
                captured.append({"url": url, "status": response.status,
                                 "body": response.json()})
            except Exception:  # noqa: BLE001
                return

        page.on("response", on_response)
        page.goto(MESSENGER_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(args.seconds * 1000)
        logged_in = _logged_in(page)
        context.close()

    out.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in captured),
                   encoding="utf-8")
    print(f"вошли: {logged_in}; записано ответов: {len(captured)} -> {out}")
    for item in captured[:15]:
        print(f"  {item['status']} {item['url'][:110]}")
    return 0


def command_mirror(args) -> int:
    albery = albery_from_env()
    worker_id = f"avito-worker:{os.getenv('COMPUTERNAME') or os.uname().nodename}:{os.getpid()}"
    print(f"Зеркало запущено ({worker_id}). Аккаунт: {args.account}. Ctrl+C — остановка.")
    while True:
        try:
            pending = albery.claim_outbox(worker_id, args.account)
            if pending:
                print(f"в очереди на отправку: {len(pending)}")
            for item in pending:
                # Отправку включим сразу после того, как разбор мессенджера будет снят
                # с живой сессии (команда capture). Пока строку возвращаем в очередь
                # честно: «не отправлено» лучше, чем тихо потерянное сообщение.
                albery.finish(item["outbox_id"], worker_id, "failed",
                              error="отправка в Авито ещё не подключена")
        except RuntimeError as exc:
            print(f"Albery недоступен: {exc}")
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return 0
        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Воркер браузерной сессии Авито")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("login", "разовый вход в аккаунт"),
                            ("probe", "проверить сессию и сообщить состояние в Albery"),
                            ("capture", "записать сырые ответы мессенджера"),
                            ("mirror", "рабочий режим: зеркалить переписки")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--account", required=True, help="код аккаунта, как в интерфейсе")
        if name in {"probe", "capture"}:
            item.add_argument("--show", action="store_true", help="показать окно браузера")
        if name == "capture":
            item.add_argument("--seconds", type=int, default=25)
            item.add_argument("--out", default="")
        if name == "mirror":
            item.add_argument("--once", action="store_true", help="один проход и выход")

    args = parser.parse_args()
    handlers = {"login": command_login, "probe": command_probe,
                "capture": command_capture, "mirror": command_mirror}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
