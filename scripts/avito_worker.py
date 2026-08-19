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
import random
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
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
               provider_message_id: str = "", external_chat_id: str = "") -> None:
        self.post(f"/api/avito-worker/outbox/{outbox_id}/result",
                  {"worker_id": worker_id, "result": result, "error": error,
                   "provider_message_id": provider_message_id,
                   "external_chat_id": external_chat_id})


def _load_local_env() -> None:
    """Настройки воркера лежат рядом с профилями, а не в репозитории и не в переменных среды.

    Токен доступа к Albery — секрет: он не попадает ни в git, ни в вывод команд, ни в
    историю оболочки. Файл создаётся один раз, читается только этой машиной.
    """
    env_file = profiles_dir() / ".env"
    if not env_file.exists():
        return
    # utf-8-sig, а не utf-8: файл нередко создают редактором или PowerShell, и они ставят
    # BOM. С обычным utf-8 первая переменная файла молча теряется — читается как «﻿KEY».
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
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


def _default_local_ip() -> str | None:
    """Каким локальным адресом машина выходит в интернет по умолчанию (обычно это VPN)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 443))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def direct_avito_ip(host: str = "www.avito.ru") -> str | None:
    """Адрес Авито, который на этой машине идёт МИМО VPN.

    У avito.ru несколько A-записей, а исключение в VPN-клиенте прописано на ту, которую он
    разрезолвил сам: остальные уходят в туннель, и оттуда прилетает «Доступ ограничен:
    проблема с IP». Браузер об этом не знает и берёт адрес как повезёт, поэтому нужный мы
    находим сами — по тому, каким локальным адресом вышло соединение.
    """
    default_ip = _default_local_ip()
    try:
        candidates = [info[4][0] for info in socket.getaddrinfo(host, 443, socket.AF_INET,
                                                                socket.SOCK_STREAM)]
    except socket.gaierror:
        return None
    for address in dict.fromkeys(candidates):
        try:
            with socket.create_connection((address, 443), timeout=10) as sock:
                if default_ip is None or sock.getsockname()[0] != default_ip:
                    return address
        except OSError:
            continue
    return None


def _browser_context(playwright, account: str, *, headless: bool):
    """Постоянный профиль: куки и localStorage живут между запусками, вход — один раз."""
    args = ["--disable-blink-features=AutomationControlled"]
    direct = direct_avito_ip()
    if direct:
        # Прибиваем имена Авито к адресу, который выпущен мимо VPN. Иначе браузер сам
        # выберет A-запись, та уйдёт в туннель, и Авито ответит блокировкой по IP.
        rules = ",".join(f"MAP {name} {direct}" for name in
                         ("avito.ru", "*.avito.ru", "avito.st", "*.avito.st"))
        args.append(f"--host-resolver-rules={rules}")
        print(f"адрес Авито мимо VPN: {direct}")
    else:
        print("ВНИМАНИЕ: прямого адреса Авито не нашлось — пойдём через VPN, "
              "возможен отказ «проблема с IP»")
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_path(account)),
        channel="chrome",
        headless=headless,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1600, "height": 900},
        args=args,
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


def _maybe_json(payload: Any) -> Any:
    """Кадр сокета — либо JSON, либо служебный текст; разбираем что можем, остальное как есть."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    text = str(payload)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text[:2000]


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
            try:
                if "application/json" not in (response.headers.get("content-type") or ""):
                    return
                captured.append({"kind": "http", "url": response.url,
                                 "status": response.status, "body": response.json()})
            except Exception:  # noqa: BLE001
                return

        def on_websocket(ws):
            # Переписку мессенджер получает сокетом, а не запросами: без кадров сокета
            # разбирать нечего.
            captured.append({"kind": "ws-open", "url": ws.url})
            ws.on("framereceived", lambda payload: captured.append(
                {"kind": "ws-in", "url": ws.url, "body": _maybe_json(payload)}))
            ws.on("framesent", lambda payload: captured.append(
                {"kind": "ws-out", "url": ws.url, "body": _maybe_json(payload)}))

        page.on("response", on_response)
        page.on("websocket", on_websocket)
        page.goto(MESSENGER_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(args.seconds * 1000)
        logged_in = _logged_in(page)
        html = page.content()
        context.close()

    out.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in captured),
                   encoding="utf-8")
    html_path = out.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    kinds: dict[str, int] = {}
    for item in captured:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"вошли: {logged_in}; записано: {kinds} -> {out}")
    print(f"HTML страницы -> {html_path} ({len(html)} символов)")
    for item in captured[:25]:
        print(f"  {item['kind']:7} {str(item.get('status') or ''):3} {item['url'][:100]}")
    return 0


def extract_state(html: str) -> dict[str, Any]:
    """Достаёт встроенное состояние мессенджера из страницы.

    Авито отдаёт мессенджер уже с данными: в одном из script-тегов лежит цельный JSON, а в
    нём — список чатов. Разбираем именно его, а не вёрстку: имена CSS-классов Авито меняет
    когда угодно, а контракт собственных данных живёт куда дольше.
    """
    # Якорь ищем регулярным выражением, а не подстрокой: подстрока держалась бы на том, что
    # Авито минифицирует JSON, и первый же пробел после «"channels":» сломал бы разбор молча.
    match = re.search(r'"channels"\s*:\s*\{\s*"ids"', html)
    if match is None:
        return {}
    anchor = match.start()
    script_start = html.rfind("<script", 0, anchor)
    if script_start < 0:
        return {}
    body_start = html.find(">", script_start) + 1
    body_end = html.find("</script>", body_start)
    try:
        return json.loads(html[body_start:body_end].strip())
    except ValueError:
        return {}


def _avito_time(value: Any) -> str | None:
    """Время Авито — в сотнях наносекунд от эпохи; переводим в обычную метку времени."""
    try:
        seconds = int(value) / 10_000_000
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _message_text(message: dict[str, Any]) -> str:
    """Текст сообщения из ответа Авито.

    preview и body приходят объектами вида {"text": "..."}, а не строками: без разбора в
    журнал попадала запись целиком — оператор видел «{'text': 'Здравствуйте…'}» вместо
    сообщения (поймано на живых данных 19.08.2026). Нетекстовые вложения называем словами,
    а не пустой строкой, иначе переписка выглядит как пропуск.
    """
    for key in ("preview", "body", "content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    kind = str(message.get("type") or "").strip()
    labels = {"image": "[изображение]", "voice": "[голосовое сообщение]",
              "file": "[файл]", "item": "[объявление]", "location": "[геопозиция]",
              "call": "[звонок]", "video": "[видео]"}
    return labels.get(kind, "")


def parse_channels(html: str) -> list[dict[str, Any]]:
    """Список переписок в том виде, в каком их принимает Albery."""
    state = extract_state(html)
    channels = (((state.get("state") or {}).get("ssrState") or {}).get("channels")) or {}
    entities = channels.get("entities") or {}
    result: list[dict[str, Any]] = []
    for channel_id in channels.get("ids") or []:
        entity = entities.get(channel_id) or {}
        users = entity.get("users") or []
        # Свой пользователь — тот, у кого есть originalId (числовой id аккаунта); собеседник
        # приходит без него. Проверено на живых чатах аккаунта владельца.
        mine = next((u for u in users if u.get("originalId")), None)
        other = next((u for u in users if u is not mine), None)
        listing = ((entity.get("context") or {}).get("value")) or {}
        last = entity.get("lastMessage") or {}
        text = _message_text(last)
        author = "operator" if (mine and last.get("fromUid") == mine.get("id")) else "client"
        messages = []
        if last.get("id") and text:
            messages.append({
                "external_message_id": str(last["id"]),
                "text": text,
                "author_type": author,
                "author_name": (mine if author == "operator" else other or {}).get("name") or "",
                "occurred_at": _avito_time(last.get("created")),
            })
        listing_id = str(listing.get("id") or "")
        result.append({
            "external_chat_id": str(channel_id),
            "display_name": str((other or {}).get("name") or "Собеседник"),
            "listing": {
                "id": listing_id,
                "title": str(listing.get("title") or listing.get("name") or ""),
                "price": str(listing.get("priceString") or listing.get("price") or ""),
                "url": f"https://www.avito.ru/{listing_id}" if listing_id else "",
            },
            "update_id": f"{channel_id}:{last.get('id') or entity.get('updated') or ''}",
            "messages": messages,
        })
    return result


REPLY_INPUT = '[data-marker="reply/input"]'
MESSAGE_TEXT = '[data-marker="messageText"]'


def _type_like_a_human(page, selector: str, text: str) -> None:
    """Печатаем с задержкой, а не вставляем разом.

    Мгновенно возникший в поле текст — самый простой признак автоматизации, а нам важно
    сохранить аккаунт живым. Задержка на символ примерно как у быстрого человека.
    """
    field = page.locator(selector).first
    field.click()
    field.type(text, delay=random.randint(18, 55))


def send_message(page, chat_id: str, text: str) -> tuple[bool, str]:
    """Отправляет сообщение в существующий чат и ПРОВЕРЯЕТ, что оно появилось в переписке.

    Возвращает (успех, пояснение). Успехом считается только увиденное в истории сообщение:
    нажатая клавиша — это ещё не доставка, а «кажется, отправилось» в журнале хуже честного
    отказа, потому что оператор перестанет перепроверять.
    """
    page.goto(f"{MESSENGER_URL}/channel/{chat_id}", wait_until="domcontentloaded", timeout=90000)
    try:
        page.wait_for_selector(REPLY_INPUT, timeout=25000)
    except Exception:  # noqa: BLE001
        if not _logged_in(page):
            return False, "сессия просрочена — нужен повторный вход"
        return False, "поле ввода не найдено: возможно, Авито поменял мессенджер"

    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    _type_like_a_human(page, REPLY_INPUT, text)
    page.keyboard.press("Enter")

    needle = text.strip()[:60]
    try:
        page.wait_for_function(
            """([selector, needle]) => Array.from(document.querySelectorAll(selector))
                   .some(node => (node.textContent || '').includes(needle))""",
            arg=[MESSAGE_TEXT, needle],
            timeout=20000,
        )
    except Exception:  # noqa: BLE001
        # Ушло или нет — неизвестно: повторять нельзя, человек может получить дубль.
        return False, "unknown: сообщение не появилось в переписке за 20 секунд"
    return True, page.url.rsplit("/", 1)[-1]


def start_chat_from_item(page, item_id: str, text: str) -> tuple[bool, str]:
    """Пишет первым автору объявления: открывает объявление, жмёт «Написать», отправляет.

    Возвращает (успех, id чата или причина). Чат создаётся самим Авито в момент отправки,
    поэтому его идентификатор забираем из адреса уже после доставки.
    """
    page.goto(f"https://www.avito.ru/{item_id}", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    if not _logged_in(page):
        return False, "сессия просрочена — нужен повторный вход"

    opened = False
    for locator in (page.locator('[data-marker="item-contact-bar/message"]'),
                    page.get_by_role("button", name=re.compile("Написать", re.I)),
                    page.get_by_role("link", name=re.compile("Написать", re.I))):
        try:
            if locator.count():
                locator.first.click(timeout=15000)
                opened = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not opened:
        return False, "кнопка «Написать» не найдена: объявление снято или изменилась страница"

    try:
        page.wait_for_selector(REPLY_INPUT, timeout=30000)
    except Exception:  # noqa: BLE001
        return False, "окно переписки не открылось"

    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    _type_like_a_human(page, REPLY_INPUT, text)
    page.keyboard.press("Enter")

    needle = text.strip()[:60]
    try:
        page.wait_for_function(
            """([selector, needle]) => Array.from(document.querySelectorAll(selector))
                   .some(node => (node.textContent || '').includes(needle))""",
            arg=[MESSAGE_TEXT, needle],
            timeout=20000,
        )
    except Exception:  # noqa: BLE001
        return False, "unknown: сообщение не появилось в переписке за 20 секунд"
    chat_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    return True, chat_id


def search_listings(page, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Поиск объявлений живой сессией.

    Берём ссылки и подписи прямо из карточек выдачи: у каждой есть разметка `item`, внутри —
    ссылка вида `/…_<номер>`. Номер объявления — то единственное, что нужно, чтобы написать
    автору; остальное (заголовок, цена) идёт для глаз человека.
    """
    page.goto(f"https://www.avito.ru/all?q={urlparse.quote(query)}",
              wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    try:
        page.wait_for_selector('[data-marker="item"]', timeout=25000)
    except Exception:  # noqa: BLE001
        return []
    cards = page.locator('[data-marker="item"]')
    found: list[dict[str, Any]] = []
    for index in range(min(cards.count(), limit * 2)):
        card = cards.nth(index)
        try:
            link = card.locator('[itemprop="url"], a[href*="_"]').first
            href = link.get_attribute("href") or ""
            match = re.search(r"_(\d{9,})", href)
            if not match:
                continue
            title = (card.locator('[itemprop="name"]').first.inner_text()
                     if card.locator('[itemprop="name"]').count() else link.inner_text())
            price = (card.locator('[itemprop="price"]').first.get_attribute("content")
                     if card.locator('[itemprop="price"]').count() else "")
            found.append({"item_id": match.group(1),
                          "title": " ".join((title or "").split())[:90],
                          "price": price or "",
                          "url": f"https://www.avito.ru{href}" if href.startswith("/") else href})
        except Exception:  # noqa: BLE001
            continue
        if len(found) >= limit:
            break
    return found


def command_search(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            found = search_listings(page, args.query, limit=args.limit)
        finally:
            context.close()
    out = profile_path(args.account) / "search.json"
    out.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"найдено объявлений: {len(found)} -> {out}")
    for item in found[:args.limit]:
        print(f"  {item['item_id']}  {item['price'] or '—':>10}  {item['title'][:60]}")
    return 0 if found else 1


def command_send(args) -> int:
    """Ручная отправка одного сообщения — для проверки боем перед включением очереди."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if args.item:
                ok, detail = start_chat_from_item(page, args.item, args.text)
            else:
                ok, detail = send_message(page, args.chat, args.text)
        finally:
            context.close()
    print(("отправлено, чат " if ok else "НЕ отправлено: ") + detail)
    return 0 if ok else 1


def command_inspect_chat(args) -> int:
    """Строение одного чата: поле ввода и кнопка отправки. Ничего не отправляет."""
    from playwright.sync_api import sync_playwright

    report: dict[str, Any] = {}
    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"{MESSENGER_URL}/channel/{args.chat}", wait_until="domcontentloaded",
                  timeout=90000)
        page.wait_for_timeout(5000)
        report["url"] = page.url
        report["logged_in"] = _logged_in(page)
        html = page.content()
        report["markers"] = sorted(set(re.findall(r'data-marker="([^"]{3,60})"', html)))
        report["textareas"] = page.locator("textarea").count()
        report["contenteditable"] = page.locator("[contenteditable='true']").count()
        report["buttons"] = [
            (page.locator("button").nth(i).get_attribute("data-marker")
             or (page.locator("button").nth(i).inner_text() or "").strip()[:30])
            for i in range(min(page.locator("button").count(), 25))
        ]
        out = profile_path(args.account) / "chat_inspect.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
    print(f"строение чата -> {out}")
    print("вошли:", report["logged_in"], "| textarea:", report["textareas"],
          "| contenteditable:", report["contenteditable"])
    print("маркеры ввода:", [m for m in report["markers"]
                             if any(k in m for k in ("input", "send", "message", "text"))][:12])
    return 0


def command_mirror(args) -> int:
    from playwright.sync_api import sync_playwright

    albery = albery_from_env()
    worker_id = f"avito-worker:{os.getenv('COMPUTERNAME') or 'pc'}:{os.getpid()}"
    print(f"Зеркало запущено ({worker_id}). Аккаунт: {args.account}. Ctrl+C — остановка.")

    with sync_playwright() as playwright:
        context = _browser_context(playwright, args.account, headless=not args.show)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            while True:
                try:
                    page.goto(MESSENGER_URL, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(2500)
                    if not _logged_in(page):
                        albery.report_session(args.account, "needs_login",
                                              "Авито просит войти заново")
                        print("сессия просрочена — нужен повторный вход (команда login)")
                        return 1
                    channels = parse_channels(page.content())
                    albery.report_session(args.account, "ok")
                    stored = 0
                    for channel in channels:
                        answer = albery.push_inbound({"account": args.account, **channel})
                        stored += int(answer.get("stored_messages") or 0)
                    print(f"переписок: {len(channels)}, новых сообщений: {stored}")
                except RuntimeError as exc:
                    print(f"Albery недоступен: {exc}")
                except Exception as exc:  # noqa: BLE001
                    print(f"сбой обхода: {type(exc).__name__}: {exc}")
                    albery.report_session(args.account, "error", str(exc)[:300])

                try:
                    pending = albery.claim_outbox(worker_id, args.account)
                    for item in pending:
                        outbox_id = item["outbox_id"]
                        # Границу побочного эффекта отмечает сервер ДО нажатия «отправить»:
                        # обрыв во время отправки не должен выглядеть как обрыв до неё.
                        if not albery.mark_sending(outbox_id, worker_id):
                            print(f"  строка {outbox_id}: отправлять нельзя, отменена")
                            continue
                        chat_id = str(item.get("external_chat_id") or "")
                        text = str(item.get("text") or "")
                        try:
                            if chat_id.startswith("item:"):
                                # Пишем первыми: чата ещё нет, его создаст Авито в момент
                                # отправки, и настоящий идентификатор вернём серверу.
                                ok, detail = start_chat_from_item(page, chat_id[5:], text)
                            else:
                                ok, detail = send_message(page, chat_id, text)
                        except Exception as exc:  # noqa: BLE001
                            ok, detail = False, f"unknown: {type(exc).__name__}: {exc}"
                        if ok:
                            albery.finish(outbox_id, worker_id, "sent", provider_message_id=detail,
                                          external_chat_id=detail)
                            print(f"  строка {outbox_id}: отправлено")
                        else:
                            # «unknown» — отдельный исход: сообщение могло уйти, и повтор
                            # показал бы человеку дубль. Такие строки разбирает оператор.
                            result = "unknown" if detail.startswith("unknown") else "failed"
                            albery.finish(outbox_id, worker_id, result, error=detail)
                            print(f"  строка {outbox_id}: {result} — {detail}")
                        time.sleep(random.uniform(2.0, 5.0))
                except RuntimeError as exc:
                    print(f"очередь недоступна: {exc}")

                if args.once:
                    return 0
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return 0
        finally:
            context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Воркер браузерной сессии Авито")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("login", "разовый вход в аккаунт"),
                            ("probe", "проверить сессию и сообщить состояние в Albery"),
                            ("capture", "записать сырые ответы мессенджера"),
                            ("inspect-chat", "показать строение окна чата"),
                            ("send", "отправить одно сообщение вручную (проверка боем)"),
                            ("search", "найти объявления по запросу"),
                            ("mirror", "рабочий режим: зеркалить переписки")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--account", required=True, help="код аккаунта, как в интерфейсе")
        if name in {"probe", "capture", "inspect-chat"}:
            item.add_argument("--show", action="store_true", help="показать окно браузера")
        if name == "inspect-chat":
            item.add_argument("--chat", required=True, help="идентификатор чата (u2i-…)")
        if name == "search":
            item.add_argument("--query", required=True, help="что ищем")
            item.add_argument("--limit", type=int, default=10)
            item.add_argument("--show", action="store_true", help="показать окно браузера")
        if name == "send":
            item.add_argument("--chat", default="", help="ответ в существующий чат (u2i-…)")
            item.add_argument("--item", default="", help="написать первым автору объявления (id)")
            item.add_argument("--text", required=True, help="текст сообщения")
            item.add_argument("--show", action="store_true", help="показать окно браузера")
        if name == "capture":
            item.add_argument("--seconds", type=int, default=25)
            item.add_argument("--out", default="")
        if name == "mirror":
            item.add_argument("--once", action="store_true", help="один проход и выход")
            item.add_argument("--show", action="store_true", help="показать окно браузера")

    args = parser.parse_args()
    handlers = {"login": command_login, "probe": command_probe,
                "capture": command_capture, "mirror": command_mirror,
                "inspect-chat": command_inspect_chat, "send": command_send,
                "search": command_search}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
