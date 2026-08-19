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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

# Консоль Windows по умолчанию cp1251: любое «×» или «₽» в тексте объявления роняло вывод
# воркера посреди отчёта об отправке. Печать не должна ронять транспорт.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

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
        # ТОЛЬКО avito.ru: статика живёт на отдельном хосте avito.st с другими адресами,
        # и подмена ломала загрузку стилей и скриптов — страница оставалась «голой», виджет
        # переписки не оживал, кнопка отправки не включалась. Проверено 19.08.2026.
        rules = ",".join(f"MAP {name} {direct}" for name in ("avito.ru", "*.avito.ru"))
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
                # Записываем и ЗАПРОС: мессенджер спрашивает данные по JSON-RPC, и без тела
                # запроса ответ невозможно повторить — видно «что пришло», но не «о чём спросили».
                captured.append({"kind": "http", "url": response.url,
                                 "status": response.status,
                                 "request": _maybe_json(response.request.post_data or ""),
                                 "body": response.json()})
            except Exception:  # noqa: BLE001
                return

        def on_websocket(ws):
            # Сокет мессенджера живёт в web-воркере, и его кадры сюда обычно НЕ попадают —
            # поэтому список чатов записываем отдельно, спросив его тем же протоколом.
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
        try:
            captured.append({"kind": "rpc", "url": RPC_CHATS, "body": rpc(page, RPC_CHATS)})
        except Exception as error:  # noqa: BLE001
            captured.append({"kind": "rpc", "url": RPC_CHATS, "body": str(error)})
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


# Мессенджер разговаривает с Авито своим протоколом — JSON-RPC. Обычно он идёт по сокету из
# web-воркера (снаружи такие запросы не видны вовсе), но у сокета есть HTTP-двойник, и он
# принимает те же методы с той же сессией браузера. Спрашиваем данные им, а не вычитываем со
# страницы: это СОБСТВЕННЫЙ контракт мессенджера, он переживает и вёрстку, и перерисовки.
RPC_URL = "/web/1/socket/fallback?app_name=web&app_version=7.596.2&id_version=v3"
RPC_CHATS = "avito.getChats.v5"
RPC_HISTORY = "messenger.history.v2"
RPC_SESSION = "avito.getSession"

_RPC_SCRIPT = """
async ([url, method, params]) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'include',
    body: JSON.stringify({jsonrpc: '2.0', id: Date.now(), method, params}),
  });
  return await response.json();
}
"""


def rpc(page, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Зовёт метод мессенджера из контекста страницы — её же сессией и с её же куками."""
    answer = page.evaluate(_RPC_SCRIPT, [RPC_URL, method, params or {}])
    if isinstance(answer, dict) and answer.get("error"):
        raise RuntimeError(f"{method}: {answer['error'].get('message') or answer['error']}")
    return (answer or {}).get("result") or {}


def own_user_id(page) -> str:
    """Числовой идентификатор нашего аккаунта — по нему отличаем свои сообщения от чужих."""
    try:
        return str(rpc(page, RPC_SESSION).get("userId") or "")
    except Exception:  # noqa: BLE001
        return ""


def item_chat_id(page, item_id: str) -> str:
    """Идентификатор чата по объявлению — по данным Авито, а не по адресу страницы.

    Со страницы объявления в чат не переходят: окно переписки открывается прямо на ней.
    Поэтому настоящий идентификатор чата берём здесь — он же нужен, чтобы связать разговор.
    """
    for channel in rpc(page, RPC_CHATS).get("channels") or []:
        context = channel.get("context") or {}
        if str(((context.get("value") or {}).get("id")) or "") == str(item_id):
            return str(channel.get("channelId") or "")
    return ""


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


def _user_original_id(user: dict[str, Any]) -> str:
    """Числовой id пользователя. У Авито он лежит в профиле, а не в самой записи участника."""
    profile = user.get("publicUserProfile") or {}
    return str(user.get("originalId") or profile.get("originalId") or "")


def parse_channels(payload: Any, *, own_id: str = "") -> list[dict[str, Any]]:
    """Список переписок в том виде, в каком их принимает Albery.

    На вход — ответ `avito.getChats.v5`. Разбор вынесен в чистую функцию: он закреплён
    тестами на синтетическом примере, настоящие переписки владельца в репозиторий не попадают.
    """
    channels = (payload or {}).get("channels") or []
    result: list[dict[str, Any]] = []
    for entity in channels:
        channel_id = str(entity.get("channelId") or "")
        if not channel_id:
            continue
        users = entity.get("users") or []
        # Свой участник — тот, чей числовой id совпадает с id нашего аккаунта. Раньше своим
        # считался «тот, у кого вообще есть originalId», но в ответе сокета он есть у обоих:
        # так вся переписка записалась бы как чужая.
        mine = next((u for u in users if own_id and _user_original_id(u) == own_id), None)
        if mine is None:
            mine = next((u for u in users if _user_original_id(u)), None)
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
                "author_name": ((mine if author == "operator" else other) or {}).get("name") or "",
                "occurred_at": _avito_time(last.get("created")),
            })
        listing_id = str(listing.get("id") or "")
        result.append({
            "external_chat_id": channel_id,
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
# Виджет переписки живёт на САМОЙ странице объявления (разметка icebreakers), отдельного окна
# нет: кнопка «Написать» лишь подводит к нему.
ITEM_MESSAGE_BUTTON = '[data-marker="messenger-button/button"]'
ICEBREAKER_INPUT = '[data-marker="icebreakers/textarea"], [data-marker="icebreakers/extended-input"]'
ICEBREAKER_SEND = '[data-marker="icebreakers/send-message"]'


def _type_like_a_human(page, selector: str, text: str) -> None:
    """Печатаем с задержкой, а не вставляем разом.

    Мгновенно возникший в поле текст — самый простой признак автоматизации, а нам важно
    сохранить аккаунт живым. Задержка на символ примерно как у быстрого человека.
    """
    field = page.locator(selector).first
    field.click()
    field.type(text, delay=random.randint(18, 55))


def _history_messages(page, chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list(rpc(page, RPC_HISTORY, {"channelId": chat_id, "limit": limit,
                                        "offset": 0}).get("items") or [])


def send_message(page, chat_id: str, text: str) -> tuple[bool, str]:
    """Отправляет сообщение в существующий чат и ПРОВЕРЯЕТ, что оно появилось в переписке.

    Возвращает (успех, пояснение). Успех — это НОВОЕ сообщение с нашим текстом в истории,
    которую отдаёт сам Авито. Нажатая клавиша — ещё не доставка, а «кажется, отправилось» в
    журнале хуже честного отказа: оператор перестанет перепроверять. Сверяем именно новые
    сообщения, а не наличие текста: тот же ответ мог уже стоять в переписке раньше.
    """
    page.goto(f"{MESSENGER_URL}/channel/{chat_id}", wait_until="domcontentloaded", timeout=90000)
    try:
        page.wait_for_selector(REPLY_INPUT, timeout=25000)
    except Exception:  # noqa: BLE001
        if not _logged_in(page):
            return False, "сессия просрочена — нужен повторный вход"
        return False, "поле ввода не найдено: возможно, Авито поменял мессенджер"

    try:
        seen = {str(m.get("id")) for m in _history_messages(page, chat_id)}
    except Exception as error:  # noqa: BLE001
        return False, f"история переписки недоступна ({error})"

    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    _type_like_a_human(page, REPLY_INPUT, text)
    page.keyboard.press("Enter")

    needle = text.strip()[:60]
    for attempt in range(4):
        page.wait_for_timeout(2000 + attempt * 2000)
        try:
            fresh = [m for m in _history_messages(page, chat_id) if str(m.get("id")) not in seen]
        except Exception as error:  # noqa: BLE001
            return False, f"unknown: история переписки недоступна ({error})"
        for message in fresh:
            if needle in _message_text(message):
                return True, str(message.get("id") or "")
    # Ушло или нет — неизвестно: повторять нельзя, человек может получить дубль.
    return False, "unknown: сообщение не появилось в переписке"


def start_chat_from_item(page, item_id: str, text: str) -> tuple[bool, str]:
    """Пишет первым автору объявления прямо со страницы объявления.

    Возвращает (успех, id чата или причина). Чат создаёт сам Авито в момент отправки, и
    настоящий его идентификатор мы СПРАШИВАЕМ У АВИТО, а не вычитываем из адреса страницы:
    перехода в чат не происходит, адрес остаётся адресом объявления. Раньше из-за этого
    разговор так и оставался с временным ключом (случай 19.08.2026, разговор 648).

    Успехом считается только чат, который Авито показывает в своём же списке. Появление
    текста на странице — не доказательство: 19.08.2026 страница показывала отправленным
    сообщение, которого у Авито не было вовсе.
    """
    page.goto(f"https://www.avito.ru/{item_id}", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    if not _logged_in(page):
        return False, "сессия просрочена — нужен повторный вход"
    if "404" in (page.title() or ""):
        return False, "объявление снято: страница отдаёт 404"

    # Кнопка «Написать» не открывает окно, а подводит к виджету переписки на той же странице.
    # Клик по ней не обязателен, но с ним виджет гарантированно попадает в поле зрения.
    button = page.locator(ITEM_MESSAGE_BUTTON).first
    try:
        if button.count():
            button.click(timeout=15000)
            page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))
    except Exception:  # noqa: BLE001
        pass

    field = page.locator(f"{ICEBREAKER_INPUT}, {REPLY_INPUT}").first
    if not field.count():
        return False, "поле переписки на объявлении не найдено: писать автору здесь нельзя"
    field.click()
    field.type(text, delay=random.randint(18, 55))
    page.wait_for_timeout(random.randint(400, 900))

    # Отправляет Enter. Кнопка виджета остаётся НЕактивной даже при заполненном поле
    # (19.08.2026: 29 попыток клика по «element is not enabled»), поэтому она — запасной путь.
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)
    send_button = page.locator(ICEBREAKER_SEND).first
    try:
        if send_button.count() and send_button.is_enabled():
            send_button.click(timeout=8000)
    except Exception:  # noqa: BLE001
        pass

    # Спрашиваем у Авито: появился ли чат по этому объявлению. Список обновляется не мгновенно.
    for attempt in range(4):
        page.wait_for_timeout(2500 + attempt * 2500)
        try:
            chat_id = item_chat_id(page, item_id)
        except Exception as error:  # noqa: BLE001
            return False, f"unknown: список чатов недоступен ({error})"
        if chat_id:
            return True, chat_id
    return False, "unknown: Авито не показывает чат по этому объявлению после отправки"


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
                    channels = parse_channels(rpc(page, RPC_CHATS), own_id=own_user_id(page))
                    albery.report_session(args.account, "ok")
                    stored = 0
                    for channel in channels:
                        answer = albery.push_inbound({"account": args.account, **channel})
                        stored += int(answer.get("stored_messages") or 0)
                        # Сшивку печатаем: разговор, заведённый «написать первым», именно
                        # здесь получает настоящий чат — по номеру объявления.
                        stitched = answer.get("stitched") or {}
                        if stitched:
                            print(f"  разговор {stitched.get('conversation_id')}: "
                                  f"{stitched.get('action')} -> {channel['external_chat_id']}")
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
