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


def main() -> int:
    import iu_form_merge
    from shared.db import connect

    if not iu_form_merge.ENABLED:
        return 0
    crm = BitrixCrm()
    with connect() as conn:
        stats = iu_form_merge.run_once(crm=crm, conn=conn)
    # Тишина в журнале, когда делать нечего: сторож ходит каждую минуту.
    if stats["merged"] or stats["unmatched"]:
        log.info("анкеты: %s", stats)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — крон не должен молчать о поломке сторожа
        log.exception("сторож анкет упал")
        sys.exit(1)
