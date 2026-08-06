"""Ограничитель ходов против настоящего PostgreSQL — доказательство главного свойства.

Юнит-тесты рядом проверяют логику на поддельном соединении. Здесь проверяется то, ради
чего вся правка затевалась: лимит держится МЕЖДУ независимыми процессами. Два экземпляра
RunSlots открывают собственные соединения к базе, поэтому advisory-локи ведут себя ровно
так же, как у двух воркеров gunicorn — это честная модель, а не имитация.

Проверяется и краш-безопасность: слот, занятый умершим процессом, освобождается сам.
Именно из-за неё выбраны advisory-локи, а не счётчик строк в таблице — счётчику
понадобился бы сборщик протухших записей, то есть ещё одна вещь, ломающаяся молча.

Marked `db`: идёт только когда есть DATABASE_URL (CI поднимает Postgres), локально пропускается.
"""
from __future__ import annotations

import pytest

from shared.run_slots import RunSlots

pytestmark = pytest.mark.db


@pytest.fixture()
def limit_two():
    """Два «воркера» с общим лимитом 2 — как два процесса gunicorn на одной коробке."""
    worker_a = RunSlots(2, poll_interval_s=0.05)
    worker_b = RunSlots(2, poll_interval_s=0.05)
    held: list = []
    yield worker_a, worker_b, held
    for handle in held:
        handle.release()


def test_limit_is_shared_between_independent_workers(limit_two):
    """СУТЬ ПРАВКИ: два воркера с лимитом 2 держат 2 слота на двоих, а не по 2 каждый.

    Со старым threading.BoundedSemaphore этот тест провалился бы: каждый экземпляр
    разрешил бы свои два хода, то есть четыре процесса hermes по 250 МБ вместо двух.
    """
    worker_a, worker_b, held = limit_two

    first = worker_a.acquire(timeout=2)
    second = worker_b.acquire(timeout=2)
    assert first is not None and second is not None
    assert first.is_local_fallback is False and second.is_local_fallback is False
    held.extend([first, second])

    assert first.index != second.index, "два хода не могут делить один слот"

    third = worker_a.acquire(timeout=0.4)
    assert third is None, "третий ход при общем лимите 2 обязан получить отказ"
    fourth = worker_b.acquire(timeout=0.4)
    assert fourth is None, "и со второго воркера тоже — лимит именно общий"


def test_released_slot_becomes_available_to_the_other_worker(limit_two):
    worker_a, worker_b, held = limit_two

    first = worker_a.acquire(timeout=2)
    second = worker_a.acquire(timeout=2)
    assert first is not None and second is not None
    assert worker_b.acquire(timeout=0.4) is None, "слоты заняты первым воркером"

    first.release()
    took = worker_b.acquire(timeout=2)
    assert took is not None, "освободившийся слот обязан достаться другому воркеру"
    held.extend([second, took])


def test_dead_process_releases_its_slot_automatically(limit_two):
    """Краш-безопасность: процесс убит OOM-killer'ом — слот не должен остаться занятым навсегда.

    Обрыв соединения без release моделирует именно это. Postgres снимает advisory-лок
    вместе с сессией, поэтому сборщик протухших слотов не нужен.
    """
    worker_a, worker_b, held = limit_two

    doomed = worker_a.acquire(timeout=2)
    survivor = worker_a.acquire(timeout=2)
    assert doomed is not None and survivor is not None
    held.append(survivor)
    assert worker_b.acquire(timeout=0.4) is None, "оба слота заняты"

    # Процесс умер: соединение оборвано, release не позвался.
    doomed._conn.close()

    recovered = worker_b.acquire(timeout=3)
    assert recovered is not None, "слот умершего процесса обязан освободиться сам"
    held.append(recovered)


def test_limit_of_one_serialises_turns():
    """Лимит 1 — крайний случай, который проще всего сломать округлением."""
    worker_a = RunSlots(1, poll_interval_s=0.05)
    worker_b = RunSlots(1, poll_interval_s=0.05)

    only = worker_a.acquire(timeout=2)
    assert only is not None
    try:
        assert worker_b.acquire(timeout=0.4) is None
    finally:
        only.release()

    after = worker_b.acquire(timeout=2)
    assert after is not None
    after.release()


def test_slots_do_not_leak_across_acquire_release_cycles():
    """Сто циклов подряд не должны исчерпать пул — иначе бот «замолчит» через сутки."""
    worker = RunSlots(2, poll_interval_s=0.05)
    for _ in range(100):
        handle = worker.acquire(timeout=2)
        assert handle is not None
        handle.release()

    both = [worker.acquire(timeout=2), worker.acquire(timeout=2)]
    assert all(h is not None for h in both), "после цикла оба слота обязаны быть свободны"
    for handle in both:
        handle.release()
