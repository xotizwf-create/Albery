"""Unit tests for the daily task check-in: stage-0 filters (the part that decides who gets
touched) and the classifier verdict parsing. DB/network-free."""
from __future__ import annotations


def _t(tid, title, resp=16, desc=""):
    return {"id": tid, "title": title, "description": desc,
            "responsible_id": resp, "creator_id": 14}


def test_filters_drop_noise_keep_value():
    import app  # noqa: F401

    import task_checkin as tc

    tasks = [
        _t(1, "Сформировать анализ по Ларетто"),                       # keep
        _t(2, "Оплатить счет за услуги связи"),                        # stop: оплат
        _t(3, "Итоги созвона 10.07, 10:00 - 10:30"),                   # stop: итоги созвона
        _t(4, "Ознакомиться с отчётом"),                               # stop: ознаком
        _t(5, "🧪 ТЕСТ авто-повтор (del)"),                            # test marker
        _t(6, "Актуализировать данные в таблицах"),                    # keep
        _t(7, "Заполнить профиль в Б24", resp=30),                     # stop-word (also massish)
        _t(8, "Написать отзывы для раздачи"),                          # keep
        _t(9, "Провести переговоры с фабриками"),                      # stop: переговор
        _t(10, "Сверить платежный календарь", resp=99),                # no access
        _t(11, "Задача с оффером"),                                    # already offered
        _t(12, "Предоставить все свои рабочие таблицы", resp=14),      # mass x3
        _t(13, "Предоставить все свои рабочие таблицы", resp=16),
        _t(14, "Предоставить все свои рабочие таблицы", resp=30),
    ]
    access = {16: True, 14: True, 30: True, 99: False}
    survivors, stats = tc.filter_tasks(tasks, offered_ids={11}, access_ok=access)
    ids = {t["id"] for t in survivors}
    assert ids == {1, 6, 8}
    assert stats["offered"] == 1
    assert stats["no_access"] == 1
    assert stats["mass"] == 3
    assert stats["test"] == 1
    assert stats["stop_word"] >= 4


def test_is_working_day_excludes_weekend():
    import datetime

    import app  # noqa: F401

    import task_checkin as tc

    # 2026-07-13 is a Monday ... 2026-07-19 is a Sunday.
    assert [tc.is_working_day(datetime.datetime(2026, 7, d)) for d in range(13, 20)] == \
        [True, True, True, True, True, False, False]


def test_classifier_parsing_tolerates_junk(monkeypatch):
    import app  # noqa: F401

    import task_checkin as tc
    import task_offers as to

    monkeypatch.setattr(to, "_groq_chat", lambda p: (
        '{"tasks": [{"id": 1, "help": true, "reason": "соберу анализ"}, '
        '{"id": "2", "help": false, "reason": ""}, {"id": "junk"}]}'))
    out = tc.classify_tasks([_t(1, "Анализ"), _t(2, "Оплата")])
    assert {"id": 1, "help": True, "reason": "соберу анализ"} in out
    assert any(v["id"] == 2 and v["help"] is False for v in out)
    assert len(out) == 2  # the junk row is dropped

    # Both engines down -> post nothing. Backoff patched to 0 so the test is fast.
    monkeypatch.setenv("B24_CHECKIN_CLASSIFY_BACKOFF_S", "0")
    monkeypatch.setattr(to, "_groq_chat", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(to, "_codex_chat", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    assert tc.classify_tasks([_t(1, "Анализ")]) == []

    # Groq down but Codex answers -> classification still works (the daily run is not lost).
    monkeypatch.setattr(to, "_codex_chat",
                        lambda p: '{"tasks":[{"id":1,"help":true,"reason":"соберу"}]}')
    out2 = tc.classify_tasks([_t(1, "Анализ")])
    assert out2 == [{"id": 1, "help": True, "reason": "соберу"}]


def test_run_checkin_dry_run_posts_nothing(monkeypatch):
    import app  # noqa: F401

    import task_checkin as tc
    import task_offers as to

    monkeypatch.setattr(tc, "_live_open_tasks", lambda: [
        _t(1, "Сформировать анализ по Ларетто"), _t(2, "Оплатить счет")])
    monkeypatch.setattr(tc, "classify_tasks",
                        lambda ts: [{"id": 1, "help": True, "reason": "сделаю анализ"}])

    posted = []
    monkeypatch.setattr(to, "_post_offer", lambda *a, **k: posted.append(a))

    import b24bot
    monkeypatch.setattr(b24bot, "_b24_main_allows", lambda uid: True)

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): pass
        def fetchall(self): return []
        def fetchone(self): return None

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(tc, "pg_connect", lambda: _Conn())
    report = tc.run_checkin(dry_run=True)
    assert report["scanned"] == 2
    assert report["passed_filters"] == 1
    assert report["picked"][0]["id"] == 1
    assert report["offers_posted"] == 0
    assert posted == []  # dry run never posts
