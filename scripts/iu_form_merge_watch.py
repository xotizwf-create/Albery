#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сторож анкет: приклеить заявку из CRM-формы к карточке человека.

Запускается кроном раз в минуту. Работает отдельным процессом, а не внутри `albery-tg`:
сторожу нужен `mcp.context_server` (инструменты CRM), а служба бота его намеренно не
импортирует — импорт тянет живые планировщики.

Решение о склейке принимает `iu_form_merge`, здесь только переходник к Битриксу и запуск.
"""
from __future__ import annotations

import logging
import os
import sys

os.environ.setdefault("B24_TASK_OFFER", "0")
os.environ.setdefault("B24_TASK_CHECKIN", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("iu-form-merge-watch")


class BitrixCrm:
    """Переходник к инструментам Битрикса. Решений не принимает — только исполняет."""

    def __init__(self):
        from mcp import context_server as cs

        self.cs = cs

    def _call(self, name: str, args: dict):
        return self.cs.TOOLS[name]["handler"](args) or {}

    def list_deals(self, *, category_id: int, limit: int):
        res = self._call("list_crm_deals", {
            "category_id": int(category_id), "limit": int(limit),
            "include_custom_fields": True, "include_closed": False,
        })
        return list(res.get("deals") or [])

    def get_deal(self, deal_id):
        res = self._call("get_crm_deal",
                         {"deal_id": int(deal_id), "include_custom_fields": True})
        return dict(res.get("deal") or res)

    def update_deal(self, deal_id, *, custom_fields=None, stage_id=None):
        args: dict = {"deal_id": int(deal_id)}
        if custom_fields:
            args["custom_fields"] = custom_fields
        if stage_id:
            args["stage"] = stage_id
        self._call("update_crm_deal", args)

    def comment(self, deal_id, text: str):
        # Инструмент ждёт именно `comment`; на `text` он отвечает отказом, и первый живой
        # прогон 29.07.2026 упал ровно здесь — уже перенеся поля.
        self._call("add_deal_comment", {"deal_id": int(deal_id), "comment": text})

    def delete_deal(self, deal_id):
        self._call("delete_crm_deal", {"deal_id": int(deal_id), "confirm": True})


def notify_bot_clients() -> int:
    """Подтвердить заполнение анкеты в том же Telegram-диалоге.

    Ключ сообщения привязан к id формовой сделки: повторный запуск/падение между
    отправкой и отметкой не создаёт дубль.
    """

    import funnel_telegram_gateway as gateway
    import funnel_workspace_store as store
    import iu_client_bot
    from shared.db import connect

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT form_deal_id, telegram_id
                  FROM iu_form_merges
                 WHERE bot_notified_at IS NULL
                   AND telegram_id IS NOT NULL
                 ORDER BY merged_at
                 LIMIT 30
                """
            )
            rows = [dict(row) for row in cur.fetchall()]
    sent = 0
    for row in rows:
        form_id = int(row["form_deal_id"])
        telegram_id = int(row["telegram_id"])
        error = ""
        try:
            conversation = store.find_conversation(
                source_key=gateway.BOT_SOURCE_KEY,
                business_connection_id="",
                external_chat_id=str(telegram_id),
            )
            if not conversation:
                raise RuntimeError("Telegram-диалог клиента не найден")
            queued = gateway._reply_to_client(
                int(conversation["id"]),
                iu_client_bot.FORM_RECEIVED,
                idempotency_key=f"iu-form-filled:{form_id}",
                reply_markup=iu_client_bot.main_menu(),
                metadata={"iu_event": "form_received", "form_deal_id": form_id},
            )
            if not queued:
                raise RuntimeError("уведомление не удалось поставить в очередь")
            sent += 1
        except Exception as exc:  # noqa: BLE001 - следующий cron повторит
            error = str(exc)[:1000]
            log.warning("подтверждение анкеты %s не поставлено в очередь: %s", form_id, error)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE iu_form_merges
                       SET bot_notified_at = CASE WHEN %s = '' THEN now() ELSE bot_notified_at END,
                           bot_notify_error = NULLIF(%s, '')
                     WHERE form_deal_id = %s
                    """,
                    (error, error, form_id),
                )
    return sent


def main() -> int:
    import iu_form_merge
    from shared.db import connect

    if not iu_form_merge.ENABLED:
        return 0
    crm = BitrixCrm()
    with connect() as conn:
        stats = iu_form_merge.run_once(crm=crm, conn=conn)
    notified = notify_bot_clients()
    # Тишина в журнале, когда делать нечего: сторож ходит каждую минуту.
    if stats["merged"] or stats["unmatched"] or notified:
        log.info("анкеты: %s; уведомлений: %s", stats, notified)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — крон не должен молчать о поломке сторожа
        log.exception("сторож анкет упал")
        sys.exit(1)
