"""Ограничитель одновременных ходов мозга должен быть ОБЩИМ для всех процессов.

Почему это блокер, а не улучшение. Каждый ход поднимает процесс `hermes` ~250 МБ на коробке
с 2 ГБ. До 06.08.2026 ограничителем был threading.BoundedSemaphore в b24bot — счётчик в
памяти одного процесса. Пока приложение однопроцессное, он работает; при переходе на
gunicorn с N воркерами каждый воркер завёл бы СВОЙ семафор и разрешал свои 3 хода:
4 воркера = 12 × 250 МБ = 3 ГБ на коробке с 2 ГБ, то есть OOM-kill на первом всплеске.
Молча — потому что снаружи всё выглядит как «лимит стоит, 3».

Здесь закреплены три вещи, на которых такой ограничитель ломается:
- слот НЕ протекает при отказе (иначе после первого «занято» лимит уползает вниз навсегда);
- падение базы не глушит бота, а откатывает к прежнему поведению;
- release идемпотентен (он зовётся из finally, который может выполниться дважды).
"""
from __future__ import annotations

import pytest

import shared.run_slots as run_slots_module
from shared.run_slots import RunSlots, RunSlotsBusy, SlotHandle, build_default, slot_probe_order


# --- порядок опроса ---------------------------------------------------------


def test_probe_order_covers_every_slot_once():
    order = slot_probe_order(4, start_at=0)
    assert sorted(order) == [0, 1, 2, 3]
    assert len(order) == len(set(order))


def test_probe_order_rotates_so_processes_do_not_fight_for_slot_zero():
    """Если все начинают с нуля, они дерутся за один слот и делают лишние запросы к базе."""
    assert slot_probe_order(3, start_at=0)[0] == 0
    assert slot_probe_order(3, start_at=1)[0] == 1
    assert slot_probe_order(3, start_at=2)[0] == 2
    assert slot_probe_order(3, start_at=3)[0] == 0, "по кругу"


def test_probe_order_handles_degenerate_limits():
    assert slot_probe_order(0, 5) == []
    assert slot_probe_order(1, 7) == [0]


# --- поведение при занятых слотах и при падении базы ------------------------


class _FakeCursor:
    def __init__(self, taken_answer):
        self._taken_answer = taken_answer
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._last = (sql, params)

    def fetchone(self):
        return {"taken": self._taken_answer()}


class _FakeConn:
    def __init__(self, taken_answer, closed_flag):
        self._taken_answer = taken_answer
        self._closed_flag = closed_flag

    def cursor(self):
        return _FakeCursor(self._taken_answer)

    def close(self):
        self._closed_flag.append(True)


def _slots_with_db(monkeypatch, taken_answer, limit=2):
    closed: list[bool] = []
    monkeypatch.setattr(
        "shared.run_slots.psycopg.connect",
        lambda *a, **kw: _FakeConn(taken_answer, closed),
    )
    monkeypatch.setattr("shared.run_slots.database_url", lambda: "postgresql://fake")
    return RunSlots(limit, poll_interval_s=0.01), closed


def test_free_slot_is_taken(monkeypatch):
    slots, _ = _slots_with_db(monkeypatch, lambda: True)
    handle = slots.acquire(timeout=1)
    assert handle is not None
    assert handle.is_local_fallback is False
    handle.release()


def test_all_slots_busy_refuses_after_timeout(monkeypatch):
    """Отказ — это правильное поведение: пользователь получает вежливое «занято»."""
    slots, _ = _slots_with_db(monkeypatch, lambda: False)
    assert slots.acquire(timeout=0.05) is None


def test_refusal_does_not_leak_the_local_slot(monkeypatch):
    """Главная ловушка: если при отказе не отпустить локальный семафор, лимит уползает вниз.

    После `limit` отказов ограничитель молча перестал бы пускать вообще кого-либо, и
    выглядело бы это как «бот перестал отвечать» без единой ошибки в журнале.
    """
    busy = {"value": True}
    slots, _ = _slots_with_db(monkeypatch, lambda: not busy["value"], limit=2)

    for _ in range(5):
        assert slots.acquire(timeout=0.02) is None

    busy["value"] = False
    handle = slots.acquire(timeout=0.5)
    assert handle is not None, "после отказов слоты обязаны быть свободны"
    handle.release()


def test_busy_connection_is_closed_not_leaked(monkeypatch):
    """Неудачная попытка обязана закрыть соединение — иначе за час набежит сотня висящих."""
    slots, closed = _slots_with_db(monkeypatch, lambda: False)
    slots.acquire(timeout=0.05)
    assert closed, "соединение после неудачной попытки должно быть закрыто"


def test_database_down_falls_back_instead_of_silencing_the_bot(monkeypatch):
    """Блип базы не должен превращаться в «бот не отвечает никому»."""
    def _boom(*a, **kw):
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr("shared.run_slots.psycopg.connect", _boom)
    monkeypatch.setattr("shared.run_slots.database_url", lambda: "postgresql://fake")
    slots = RunSlots(2, poll_interval_s=0.01)

    handle = slots.acquire(timeout=0.05)
    assert handle is not None
    assert handle.is_local_fallback is True, "работаем по локальному счётчику, как раньше"
    handle.release()


def test_local_limit_still_caps_when_database_is_down(monkeypatch):
    """В запасном режиме лимит своего процесса обязан продолжать работать."""
    monkeypatch.setattr(
        "shared.run_slots.psycopg.connect",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")),
    )
    monkeypatch.setattr("shared.run_slots.database_url", lambda: "postgresql://fake")
    slots = RunSlots(2, poll_interval_s=0.01)

    first = slots.acquire(timeout=0.05)
    second = slots.acquire(timeout=0.05)
    assert first is not None and second is not None
    assert slots.acquire(timeout=0.05) is None, "третий ход при лимите 2 пускать нельзя"
    first.release()
    assert slots.acquire(timeout=0.05) is not None, "после release слот освободился"


# --- release ----------------------------------------------------------------


def test_release_is_idempotent():
    """release зовётся из finally; повторный вызов не должен ронять ход и портить счётчик."""
    import threading

    local = threading.BoundedSemaphore(1)
    local.acquire()
    handle = SlotHandle(0, None, local)
    handle.release()
    handle.release()
    assert local.acquire(timeout=0.1) is True


# --- общий пул на всю коробку ----------------------------------------------


def test_build_default_returns_one_shared_pool(monkeypatch):
    """b24bot и tg_agent обязаны брать ОДИН пул, а не два своих.

    До 06.08.2026 у каждого был свой BoundedSemaphore(3): коробка с 2 ГБ могла поднять
    6 процессов hermes по ~250 МБ при заявленном лимите 3. Память принадлежит железу,
    а не отдельной службе.
    """
    monkeypatch.setattr(run_slots_module, "_default", None)
    first = build_default()
    second = build_default()
    assert first is second, "второй вызов обязан вернуть тот же пул, а не завести новый счёт"


def test_pool_limit_comes_from_one_env_var(monkeypatch):
    monkeypatch.setattr(run_slots_module, "_default", None)
    monkeypatch.setenv("B24_HERMES_MAX_CONCURRENCY", "5")
    assert build_default().limit == 5


# Замерено на проде 07.08.2026: живой ход обработки зум-созвона занял 396 МБ.
# Раньше в расчётах фигурировали 250 МБ — заниженная цифра, из-за неё лимит стоял 3.
HEAVY_TURN_MB = 396
FREE_MEMORY_MB = 890  # свободно на коробке 2 ГБ при спокойной работе всех служб


def test_default_limit_fits_the_memory_budget(monkeypatch):
    """Лимит по умолчанию обязан помещаться в память коробки по ТЯЖЁЛОМУ ходу.

    Считать по среднему нельзя: именно так лимит и оказался равен 3 при потребности
    1188 МБ на 890 МБ свободных. Убийств по памяти не случилось только потому, что
    нагрузка редкая (пик 10 ходов в час) и три одновременных ни разу не совпали —
    это везение, а не запас.
    """
    monkeypatch.setattr(run_slots_module, "_default", None)
    monkeypatch.delenv("B24_HERMES_MAX_CONCURRENCY", raising=False)
    limit = build_default().limit

    assert limit * HEAVY_TURN_MB <= FREE_MEMORY_MB, (
        f"лимит {limit} × {HEAVY_TURN_MB} МБ = {limit * HEAVY_TURN_MB} МБ при "
        f"{FREE_MEMORY_MB} МБ свободных — коробка уйдёт в своп. Поднимать лимит можно "
        "только вместе с памятью или после замера, что ходы стали легче."
    )
    assert limit >= 2, "меньше двух — очередь из одного человека, это уже деградация"


def test_both_places_declare_the_same_default():
    """b24bot и пул обязаны иметь ОДНО умолчание.

    Разъедутся — и лимит будет зависеть от того, чей код спросили первым, а заметить это
    можно будет только по свопу под нагрузкой.
    """
    import re
    from pathlib import Path

    defaults = set()
    for name in ("b24bot.py", "shared/run_slots.py"):
        source = Path(name).read_text(encoding="utf-8")
        found = re.findall(r'B24_HERMES_MAX_CONCURRENCY",\s*"(\d+)"', source)
        assert found, f"{name}: умолчание лимита не найдено"
        defaults.update(found)
    assert len(defaults) == 1, f"умолчания разъехались: {defaults}"


# --- контекстный менеджер ---------------------------------------------------


def test_held_raises_instead_of_silently_running_over_the_limit(monkeypatch):
    """Отказ обязан быть громким: молчаливый пропуск и есть смерть ограничителя."""
    slots, _ = _slots_with_db(monkeypatch, lambda: False)
    with pytest.raises(RunSlotsBusy):
        with slots.held(timeout=0.05):
            pytest.fail("тело не должно выполниться при занятых слотах")


def test_held_releases_the_slot_when_the_turn_raises(monkeypatch):
    """Ход упал с исключением — слот всё равно обязан вернуться в пул."""
    slots, _ = _slots_with_db(monkeypatch, lambda: True)
    with pytest.raises(ZeroDivisionError):
        with slots.held(timeout=1):
            1 / 0
    after = slots.acquire(timeout=1)
    assert after is not None, "после исключения слот должен быть свободен"
    after.release()


def test_release_survives_a_dead_connection():
    """Соединение могло умереть вместе с базой — release обязан пережить это молча.

    Лок при этом освободит сам Postgres, когда заметит разорванную сессию.
    """
    class _DeadConn:
        def cursor(self):
            raise RuntimeError("connection already closed")

        def close(self):
            raise RuntimeError("still dead")

    handle = SlotHandle(1, _DeadConn(), None)
    handle.release()  # не должно бросить
