"""Межпроцессный ограничитель одновременных тяжёлых прогонов.

Зачем. Каждый ход мозга поднимает отдельный процесс `hermes` (~250 МБ) на коробке с 2 ГБ,
поэтому число одновременных ходов ограничено. До 06.08.2026 ограничителем был
`threading.BoundedSemaphore` внутри b24bot — счётчик в памяти ОДНОГО процесса. Пока
приложение крутится единственным процессом Flask, это работает.

Как только appserver станет многопроцессным (gunicorn с N воркерами — ближайший шаг плана),
такой счётчик молча перестаёт быть ограничителем: каждый воркер заводит СВОЙ семафор и
разрешает свои 3 хода. Четыре воркера = 12 процессов hermes = 3 ГБ на коробке с 2 ГБ,
то есть OOM-kill на первом же всплеске. Это блокер, а не косметика: переезд на gunicorn
без этой правки уронил бы прод сразу.

Механизм. Пул слотов на advisory-локах PostgreSQL: слот занят, если сессия держит
`pg_try_advisory_lock(namespace, index)`. Выбран именно этот механизм, а не счётчик строк
в таблице, ровно из-за падений: advisory-лок привязан к СЕССИИ, и когда процесс умирает
(в том числе от OOM-killer), соединение рвётся и Postgres освобождает слот сам. Счётчику
в таблице понадобился бы отдельный сборщик протухших строк — то есть ещё одна вещь,
которая может сломаться молча.

Соединение под слот — выделенное, НЕ из пула: лок живёт в сессии, а соединение из пула
вернулось бы в общий доступ вместе с локом.

Отказ базы не должен глушить бота: если соединение не поднялось, ограничитель откатывается
на локальный семафор (то есть на прежнее поведение) и говорит об этом в лог.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from shared.db import _session_options, database_url

# Произвольная, но СТАБИЛЬНАЯ константа: первый ключ advisory-лока. Меняя её, вы обнуляете
# все занятые слоты, поэтому она не должна зависеть от версии кода или имени хоста.
ADVISORY_NAMESPACE = 0x41424559  # 'ABEY'


def slot_probe_order(limit: int, start_at: int) -> list[int]:
    """Порядок опроса слотов: с плавающей точки старта, по кругу.

    Если все процессы всегда начинают с нуля, они дерутся за один и тот же слот и
    отбраковываются по очереди — лишние обращения к базе на каждом ходу. Смещение
    старта разводит их по разным слотам с первой попытки.
    """
    if limit <= 0:
        return []
    offset = start_at % limit
    return [(offset + i) % limit for i in range(limit)]


class SlotHandle:
    """Занятый слот. Освобождается ровно один раз, повторный release безвреден."""

    def __init__(self, index: int, conn: Any | None, local: threading.BoundedSemaphore | None):
        self.index = index
        self._conn = conn
        self._local = local
        self._released = False

    @property
    def is_local_fallback(self) -> bool:
        return self._conn is None

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._local is not None:
            try:
                self._local.release()
            except ValueError:  # noqa: PERF203 - перерелиз семафора не должен ронять ход
                logging.warning("run_slots: локальный семафор освобождён дважды")
        if self._conn is not None:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(%s, %s)", (ADVISORY_NAMESPACE, self.index)
                    )
            except Exception:  # noqa: BLE001 - закрытие соединения освободит лок в любом случае
                logging.warning("run_slots: явный unlock не прошёл, полагаемся на закрытие", exc_info=True)
            finally:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass


class RunSlots:
    """Пул из `limit` слотов, общий для ВСЕХ процессов приложения."""

    def __init__(self, limit: int, poll_interval_s: float = 0.5):
        self.limit = max(1, int(limit))
        self.poll_interval_s = poll_interval_s
        # Локальный семафор — запасной путь на случай недоступной базы, а также страховка
        # от того, что один процесс займёт все слоты своими потоками.
        self._local = threading.BoundedSemaphore(self.limit)
        self._probe_counter = 0
        self._counter_lock = threading.Lock()

    def _next_start(self) -> int:
        with self._counter_lock:
            self._probe_counter += 1
            return self._probe_counter

    def _try_take_shared_slot(self) -> tuple[SlotHandle | None, bool]:
        """Одна попытка занять слот в базе.

        Возвращает (слот, доступна_ли_база). Различать эти два случая обязательно:
        «все слоты заняты» — это честный отказ после ожидания, а «база недоступна» —
        повод откатиться на прежнее поведение, а не глушить бота целиком.
        """
        try:
            conn = psycopg.connect(
                database_url(), row_factory=dict_row, options=_session_options(), autocommit=True
            )
        except Exception:  # noqa: BLE001
            logging.warning("run_slots: база недоступна, откат на локальный счётчик", exc_info=True)
            return None, False
        try:
            for index in slot_probe_order(self.limit, self._next_start()):
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_try_advisory_lock(%s, %s) AS taken",
                        (ADVISORY_NAMESPACE, index),
                    )
                    row = cur.fetchone() or {}
                if row.get("taken"):
                    return SlotHandle(index, conn, self._local), True
        except Exception:  # noqa: BLE001
            logging.warning("run_slots: опрос слотов упал", exc_info=True)
            conn.close()
            return None, False
        conn.close()
        return None, True

    def acquire(self, timeout: float) -> SlotHandle | None:
        """Занять слот, ожидая не дольше timeout секунд. None = все слоты заняты (отказ)."""
        deadline = time.monotonic() + max(0.0, timeout)
        # Локальный семафор берём первым: он отсекает всплеск внутри своего процесса, не
        # открывая соединений, и служит запасным путём при недоступной базе.
        if not self._local.acquire(timeout=max(0.0, timeout)):
            return None
        while True:
            handle, db_ok = self._try_take_shared_slot()
            if handle is not None:
                return handle
            if not db_ok:
                # Прежнее поведение: считаем ходы в памяти своего процесса. Хуже, чем общий
                # лимит, но лучше, чем отказать всем пользователям из-за блипа базы.
                return SlotHandle(-1, None, self._local)
            if time.monotonic() >= deadline:
                # Слоты заняты ДРУГИМИ процессами — честный отказ, как раньше делал семафор.
                self._local.release()
                return None
            time.sleep(self.poll_interval_s)


    @contextmanager
    def held(self, timeout: float) -> Iterator[SlotHandle]:
        """`with slots.held(180):` — для вызывающих, которым не нужен разбор отказа.

        Отказ здесь — исключение, потому что молча пропустить ход мимо лимита нельзя:
        именно так ограничитель и перестаёт быть ограничителем.
        """
        handle = self.acquire(timeout)
        if handle is None:
            raise RunSlotsBusy(f"свободный слот не появился за {timeout:.0f}с (лимит {self.limit})")
        try:
            yield handle
        finally:
            handle.release()


class RunSlotsBusy(RuntimeError):
    """Все слоты заняты дольше отведённого ожидания."""


_default: RunSlots | None = None
_default_lock = threading.Lock()


def build_default() -> RunSlots:
    """ОДИН пул на всю коробку, общий для appserver и Telegram-агента.

    Оба поднимают процессы `hermes` по ~250 МБ на одной и той же коробке с 2 ГБ, но до
    06.08.2026 считали их независимо: b24bot разрешал свои 3 и tg_agent (отдельная служба
    albery-tg) — свои 3. То есть фактический потолок был 6 ходов ≈ 1.5 ГБ, а не заявленные 3.
    Лимит принадлежит ЖЕЛЕЗУ, а не отдельной службе, поэтому пул здесь общий; TG_AGENT_PARALLEL_TURNS
    больше не заводит второй счётчик.
    """
    global _default
    with _default_lock:
        if _default is None:
            limit = max(1, int(os.getenv("B24_HERMES_MAX_CONCURRENCY", "3") or "3"))
            _default = RunSlots(limit)
        return _default
