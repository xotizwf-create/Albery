"""Weekly digest of the watched public Telegram channels for the owner (albery-tg-digest.timer).

Reads each channel's public web preview (https://t.me/s/<name> — no account or membership
needed, works for public channels with preview enabled), keeps the posts of the last
DIGEST_DAYS days, and asks the brain for an Albery-focused review: маркетплейсы/WB,
оргрешения и управленческие практики, конкретные «предлагаю внедрить/обновить у нас».
The result goes to the owner's chat via the tg_agent bot. Run directly or via /digest.
"""
from __future__ import annotations

import html as html_lib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

import tg_agent

log = logging.getLogger("tg_digest")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_MSG_SPLIT_RE = re.compile(r'<div class="tgme_widget_message_wrap', re.IGNORECASE)
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<br\s*/?>|</div>|</p>", re.IGNORECASE)
_ANYTAG_RE = re.compile(r"<[^>]+>")


def _clean_html(fragment: str) -> str:
    text = _TAG_RE.sub("\n", fragment)
    text = _ANYTAG_RE.sub("", text)
    text = html_lib.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_channel_preview(page_html: str) -> list[tuple[datetime, str]]:
    """(post datetime UTC, post text) for every text post on a t.me/s/<name> preview page."""
    posts: list[tuple[datetime, str]] = []
    for block in _MSG_SPLIT_RE.split(page_html)[1:]:
        time_match = _TIME_RE.search(block)
        text_match = _TEXT_RE.search(block)
        if not time_match or not text_match:
            continue
        try:
            at = datetime.fromisoformat(time_match.group(1))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        text = _clean_html(text_match.group(1))
        if text:
            posts.append((at.astimezone(timezone.utc), text))
    return posts


def fetch_channel_posts(name: str, since: datetime,
                        per_channel_cap: int = 9000) -> tuple[list[str], str | None]:
    """Fresh posts (newest last) of a public channel since `since`; (posts, error)."""
    try:
        resp = requests.get(f"https://t.me/s/{name}", headers={"User-Agent": _UA}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return [], f"сеть: {str(exc)[:120]}"
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code}"
    posts = parse_channel_preview(resp.text)
    if not posts:
        return [], "нет веб-превью (канал приватный или превью отключено)"
    fresh = [(at, tx) for at, tx in posts if at >= since]
    fresh.sort(key=lambda p: p[0])
    out, used = [], 0
    for at, tx in reversed(fresh):  # newest first so the cap keeps the most recent posts
        piece = f"[{at.strftime('%d.%m %H:%M')}] {tx[:1200]}"
        if used + len(piece) > per_channel_cap:
            break
        out.append(piece)
        used += len(piece)
    return list(reversed(out)), None


DIGEST_PROMPT_HEAD = (
    "Ты — аналитик компании Albery (продажи на Wildberries и других маркетплейсах). Ниже — свежие "
    "посты из Telegram-каналов за неделю. Подготовь ОБЗОР ДЛЯ ВЛАДЕЛЬЦА обычным текстом (без "
    "markdown-символов), кратко и по делу:\n"
    "1) Ситуация на WB/маркетплейсах — что из новостей реально касается нас (изменения правил, "
    "комиссии, логистика, риски).\n"
    "2) Управленческие и оргрешения из постов, которые стоит перенять.\n"
    "3) ГЛАВНОЕ — раздел «Предлагаю внедрить/обновить у нас»: конкретные пункты с коротким "
    "обоснованием и источником (имя канала).\n"
    "Если по разделу ничего значимого — честно напиши «ничего важного». Не выдумывай факты, "
    "опирайся только на приведённые посты."
)


def run_digest(notify_chat=None) -> str:
    tg_agent._load_env_file()
    names = tg_agent.channels()
    targets = [notify_chat] if notify_chat else tg_agent.delivery_targets()
    if not targets:
        log.warning("digest: no delivery targets yet (owner has not written to the bot)")
        return "no targets"
    if not names:
        for chat in targets:
            tg_agent.send_text(chat, "Еженедельный обзор: список каналов пуст — добавьте их "
                                     "командой /add_channel @канал.")
        return "no channels"
    days = int(os.getenv("TG_DIGEST_DAYS", "7"))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sections, problems = [], []
    for name in names:
        posts, err = fetch_channel_posts(name, since)
        if err:
            problems.append(f"t.me/{name} — {err}")
            continue
        if posts:
            sections.append(f"=== Канал t.me/{name} ({len(posts)} постов) ===\n" + "\n\n".join(posts))
        else:
            problems.append(f"t.me/{name} — за {days} дн. новых постов нет")
    if not sections:
        text = "Еженедельный обзор: свежих постов нет.\n" + "\n".join(problems)
        for chat in targets:
            tg_agent.send_text(chat, text)
        return "no posts"

    corpus = "\n\n".join(sections)[:60000]
    prompt = f"{DIGEST_PROMPT_HEAD}\n\nПериод: последние {days} дней.\n\n{corpus}"
    answer = tg_agent.hermes_answer(prompt, "tg-digest", toolsets="web",
                                    timeout_s=int(os.getenv("TG_DIGEST_HERMES_TIMEOUT", "540")))
    header = f"📰 Обзор каналов за {days} дн. ({datetime.now().strftime('%d.%m.%Y')})\n\n"
    footer = ("\n\n⚠️ Не прочитал: " + "; ".join(problems)) if problems else ""
    for chat in targets:
        tg_agent.send_text(chat, header + answer + footer)
    return "ok"


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    print(run_digest())
