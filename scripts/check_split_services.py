#!/usr/bin/env python3
"""Проверка ролей ПЕРЕД переключением nginx. Запускать на сервере после старта служб.

Написан после разбора 07.08.2026. Роли web и mcp тогда были признаны здоровыми по признакам
«порт слушает», «/login отдаёт 200», «/mcp отдаёт 401» — и все три оказались бесполезны:
ни один из этих адресов не доходит до базы, а сломан был именно пул соединений (--preload
создавал его в мастере до раздвоения на воркеров). Центр Агента лёг сразу после переключения
трафика, каждый запрос падал через 30 секунд с PoolTimeout.

Поэтому здесь проверяется то, что ломается на самом деле:
- процесс ДОХОДИТ ДО БАЗЫ (/healthz, а не /login);
- роль объявлена верно, то есть фоновые расписания не задвоятся;
- ответ приходит быстро — сломанный пул отвечает через 30 секунд, здоровый за десятки мс;
- запас держится под нагрузкой в несколько одновременных запросов, а не на одиночном.

Ненулевой код возврата = переключать nginx НЕЛЬЗЯ.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROLES = (("web", 5003), ("mcp", 5004))
SLOW_SECONDS = 5.0          # здоровый /healthz укладывается в десятки миллисекунд
CONCURRENT_PROBES = 8       # больше, чем потоков у одного воркера


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Не ходить за редиректом.

    urllib по умолчанию идёт по 302 и отдаёт 200 со страницей входа — проверка тогда видит
    «200, всё хорошо» там, где на деле адрес перехвачен авторизацией. Редирект здесь сам по
    себе провал, и он должен быть виден как редирект.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102, ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(port: int, path: str, timeout: float = 40.0) -> tuple[int, str, float]:
    started = time.monotonic()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace"), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), time.monotonic() - started
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}", time.monotonic() - started


def main() -> int:
    failures: list[str] = []

    for role, port in ROLES:
        print(f"=== роль {role} (порт {port}) ===")

        status, body, elapsed = _get(port, "/healthz")
        if status != 200:
            failures.append(f"{role}: /healthz вернул {status} за {elapsed:.1f}с — {body[:200]}")
            print(f"  [ПРОВАЛ] /healthz: {status} за {elapsed:.1f}с")
            print(f"           {body[:200]}")
            continue

        try:
            payload = json.loads(body)
        except ValueError:
            # 200 с HTML вместо JSON — это перехват сторонним обработчиком, например уводом
            # на страницу входа. Проверка обязана назвать это провалом, а не упасть сама:
            # упавшая проверка на глаз неотличима от «всё хорошо» (07.08.2026).
            failures.append(
                f"{role}: /healthz отдал не JSON — вероятно, перехвачен авторизацией или SPA. "
                f"Первые 120 символов: {body[:120]!r}"
            )
            print(f"  [ПРОВАЛ] /healthz вернул не JSON: {body[:120]!r}")
            continue

        print(f"  [OK  ] /healthz: {status} за {elapsed:.2f}с, база: {payload.get('database')}")

        if payload.get("database") != "ok":
            failures.append(f"{role}: база недоступна — {body[:200]}")
        if payload.get("role") != role:
            failures.append(
                f"{role}: процесс считает себя ролью {payload.get('role')!r}. "
                "Неверная роль = второй комплект фоновых расписаний и двойные уведомления людям."
            )
            print(f"  [ПРОВАЛ] роль объявлена как {payload.get('role')!r}, ожидалась {role!r}")
        else:
            print(f"  [OK  ] роль объявлена верно: {payload.get('role')}")

        if elapsed > SLOW_SECONDS:
            failures.append(f"{role}: /healthz отвечал {elapsed:.1f}с — похоже на проблему с пулом")

        # Одиночный запрос мог попасть на единственный живой воркер. Сломанный пул проявляется
        # именно под несколькими одновременными: свободных соединений не остаётся.
        with ThreadPoolExecutor(max_workers=CONCURRENT_PROBES) as pool:
            results = list(pool.map(lambda _: _get(port, "/healthz"), range(CONCURRENT_PROBES)))
        bad = [(s, b[:80], t) for s, b, t in results if s != 200]
        slowest = max(t for _, _, t in results)
        if bad:
            failures.append(f"{role}: {len(bad)} из {CONCURRENT_PROBES} одновременных запросов не прошли: {bad[:2]}")
            print(f"  [ПРОВАЛ] одновременных {CONCURRENT_PROBES}: провалов {len(bad)}")
        else:
            print(f"  [OK  ] одновременных {CONCURRENT_PROBES}: все 200, самый долгий {slowest:.2f}с")
            if slowest > SLOW_SECONDS:
                failures.append(f"{role}: под нагрузкой самый долгий ответ {slowest:.1f}с")

    print()
    if failures:
        print("=" * 72)
        print("ПЕРЕКЛЮЧАТЬ NGINX НЕЛЬЗЯ:")
        for line in failures:
            print(f"  · {line}")
        print("=" * 72)
        return 1
    print("=" * 72)
    print("ВСЕ РОЛИ ЗДОРОВЫ И ДОХОДЯТ ДО БАЗЫ — переключение nginx допустимо.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
