#!/usr/bin/env python3
"""Manage who may DM the Albery Hermes Telegram agent — from Telegram itself.

The real DM-access gate is the env var ``TELEGRAM_ALLOWED_USERS`` in
``/root/.hermes/.env`` (read by gateway ``_is_user_authorized``; default-deny
for everyone else). We keep ``telegram.allowed_chats`` in
``/root/.hermes/config.yaml`` in sync for consistency, **preserving group
(negative) chat IDs** which are delivery infrastructure (home channel /
team group), not users.

This is intentionally a small, line-targeted editor (no YAML round-trip) so a
large production config is never reordered or reformatted. Every edit is backed
up first, and the gateway is restarted so the new allowlist takes effect.

Usage (run on the box, as root):
    python3 tg_access.py list
    python3 tg_access.py add <telegram_user_id> ["Name"]
    python3 tg_access.py remove <telegram_user_id>
    python3 tg_access.py whoami            # print recent unknown DM senders from the log

A Telegram bot cannot resolve an @username until that person has messaged the
bot, so to grant access to a new person: have them press /start, then run
``whoami`` to read their numeric ID from the gateway log and ``add`` it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes"))
ENV_PATH = HERMES_HOME / ".env"
CONFIG_PATH = HERMES_HOME / "config.yaml"
NAMES_PATH = HERMES_HOME / "tg_access_names.json"
LOG_PATH = HERMES_HOME / "logs" / "gateway.log"

# The owner must never be locked out of their own agent.
OWNER_ID = "1451982360"  # Александр Никитенко (@alexxandrn)
# Friendly names we already know (id -> name); merged with NAMES_PATH.
KNOWN_NAMES = {
    "1451982360": "Александр Никитенко (@alexxandrn)",
    "6514126096": "Евгений Палей (@Evgenii_Pal)",
}

ENV_KEY = "TELEGRAM_ALLOWED_USERS"
CFG_USERS_KEY = "allowed_chats"  # under the `telegram:` block; mixes user + group ids


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _backup(p: Path) -> Path:
    bak = p.with_suffix(p.suffix + f".bak.{int(time.time())}")
    bak.write_text(_read(p), encoding="utf-8")
    os.chmod(bak, 0o600)
    return bak


def _atomic_write(p: Path, text: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


# ---- names map -------------------------------------------------------------

def load_names() -> dict[str, str]:
    names = dict(KNOWN_NAMES)
    if NAMES_PATH.exists():
        try:
            names.update(json.loads(_read(NAMES_PATH)))
        except Exception:
            pass
    return names


def save_name(uid: str, name: str | None) -> None:
    if not name:
        return
    data = {}
    if NAMES_PATH.exists():
        try:
            data = json.loads(_read(NAMES_PATH))
        except Exception:
            data = {}
    data[uid] = name
    _atomic_write(NAMES_PATH, json.dumps(data, ensure_ascii=False, indent=2))


# ---- current allowlist (source of truth = env) -----------------------------

def env_user_ids() -> list[str]:
    if not ENV_PATH.exists():
        return []
    for line in _read(ENV_PATH).splitlines():
        if line.startswith(ENV_KEY + "="):
            raw = line.split("=", 1)[1].strip()
            return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def _split_user_group(ids: list[str]) -> tuple[list[str], list[str]]:
    """Positive ids = users; negative ids = group/channel chats."""
    users, groups = [], []
    for i in ids:
        (groups if i.lstrip("-").isdigit() and i.startswith("-") else users).append(i)
    return users, groups


# ---- writers (line-targeted, preserve everything else) ---------------------

def write_env_users(user_ids: list[str]) -> None:
    lines = _read(ENV_PATH).splitlines()
    new_val = ",".join(user_ids)
    found = False
    for idx, line in enumerate(lines):
        if line.startswith(ENV_KEY + "="):
            lines[idx] = f"{ENV_KEY}={new_val}"
            found = True
            break
    if not found:
        lines.append(f"{ENV_KEY}={new_val}")
    _atomic_write(ENV_PATH, "\n".join(lines) + "\n")


def write_config_users(user_ids: list[str]) -> None:
    """Update `allowed_chats` under the telegram block: replace the user (positive)
    ids with `user_ids`, keep the group (negative) ids exactly as they were."""
    if not CONFIG_PATH.exists():
        return
    text = _read(CONFIG_PATH)
    # Match the telegram block's allowed_chats line (two-space indent in this config).
    pat = re.compile(r"^(?P<indent>[ \t]+)allowed_chats:[ \t]*(?P<val>.*)$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return
    cur = [x.strip() for x in m.group("val").split(",") if x.strip()]
    _, groups = _split_user_group(cur)
    merged = user_ids + groups
    new_line = f"{m.group('indent')}{CFG_USERS_KEY}: {','.join(merged)}"
    new_text = text[: m.start()] + new_line + text[m.end():]
    _atomic_write(CONFIG_PATH, new_text)


def restart_gateway() -> str:
    try:
        subprocess.run(["systemctl", "restart", "hermes-gateway"], check=True, timeout=120)
        out = subprocess.run(["systemctl", "is-active", "hermes-gateway"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as exc:  # pragma: no cover
        return f"restart-error: {exc}"


# ---- commands --------------------------------------------------------------

def cmd_list() -> int:
    names = load_names()
    ids = env_user_ids()
    users, _ = _split_user_group(ids)
    print("Доступ к Telegram-агенту (DM) имеют:")
    if not users:
        print("  (никто — allowlist пуст, агент отвечает только этим людям)")
    for uid in users:
        tag = " — владелец" if uid == OWNER_ID else ""
        print(f"  • {uid} — {names.get(uid, 'без имени')}{tag}")
    return 0


def cmd_add(uid: str, name: str | None) -> int:
    if not uid.isdigit():
        print(f"Ошибка: ID должен быть числом (Telegram user id), получено: {uid!r}")
        return 2
    users, _ = _split_user_group(env_user_ids())
    if uid in users:
        print(f"{uid} уже имеет доступ.")
        return 0
    _backup(ENV_PATH)
    if CONFIG_PATH.exists():
        _backup(CONFIG_PATH)
    users.append(uid)
    write_env_users(users)
    write_config_users(users)
    save_name(uid, name)
    status = restart_gateway()
    nm = name or load_names().get(uid, "новый пользователь")
    print(f"✅ Доступ выдан: {uid} ({nm}). Gateway: {status}.")
    print("Скажите человеку нажать /start в чате с ботом — после этого он сможет писать агенту.")
    return 0


def cmd_remove(uid: str) -> int:
    if uid == OWNER_ID:
        print("Нельзя удалить владельца — это заблокирует ваш собственный доступ.")
        return 2
    users, _ = _split_user_group(env_user_ids())
    if uid not in users:
        print(f"{uid} и так не имеет доступа.")
        return 0
    _backup(ENV_PATH)
    if CONFIG_PATH.exists():
        _backup(CONFIG_PATH)
    users = [u for u in users if u != uid]
    write_env_users(users)
    write_config_users(users)
    status = restart_gateway()
    print(f"✅ Доступ отозван: {uid}. Gateway: {status}.")
    return 0


def cmd_whoami() -> int:
    """Surface recent DM senders (esp. unauthorized) so the owner can find a new
    person's numeric ID after they press /start."""
    if not LOG_PATH.exists():
        print(f"Лог не найден: {LOG_PATH}")
        return 1
    pat = re.compile(r"(user|from|sender)[ _]?id[\"':= ]+(\d{5,})", re.IGNORECASE)
    seen: dict[str, int] = {}
    try:
        tail = subprocess.run(["tail", "-n", "4000", str(LOG_PATH)],
                              capture_output=True, text=True, timeout=30).stdout
    except Exception:
        tail = _read(LOG_PATH)[-400_000:]
    for line in tail.splitlines():
        for m in pat.finditer(line):
            seen[m.group(2)] = seen.get(m.group(2), 0) + 1
    names = load_names()
    allowed = set(env_user_ids())
    print("Недавние Telegram-отправители (из лога):")
    for uid, cnt in sorted(seen.items(), key=lambda kv: -kv[1]):
        mark = "✅ есть доступ" if uid in allowed else "⛔ нет доступа"
        print(f"  • {uid} — {names.get(uid, '?')} [{mark}] (упоминаний: {cnt})")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, *rest = argv
    if cmd == "list":
        return cmd_list()
    if cmd == "add":
        if not rest:
            print("Использование: add <telegram_user_id> [\"Имя\"]")
            return 2
        return cmd_add(rest[0], " ".join(rest[1:]) or None)
    if cmd == "remove":
        if not rest:
            print("Использование: remove <telegram_user_id>")
            return 2
        return cmd_remove(rest[0])
    if cmd == "whoami":
        return cmd_whoami()
    print(f"Неизвестная команда: {cmd}. Доступно: list | add | remove | whoami")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
