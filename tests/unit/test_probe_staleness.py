"""Устаревшее измерение нельзя показывать как текущее состояние.

17.08.2026 владелец прислал скриншот: «Bitrix REST — не отвечает», «Zoom API — токен не
выдаётся». Прямая проверка на проде в тот же момент: Битрикс отвечает за 0,13 с, Zoom
выдаёт токен за 0,24 с. В журнале — НИ ОДНОЙ неудачной пробы за сутки.

Причина: пробы обновляются в фоне, а запрос забирает ПРЕДЫДУЩЕЕ значение. Веб-роль не
перезапускалась с 16.08, и после долгого простоя страницы в кэше лежал результат
вчерашнего сетевого сбоя — он и показывался как сегодняшнее состояние. То есть монитор
не «ошибся», он честно показал старое, не сказав, что оно старое.

Врать про аварию так же плохо, как врать про здоровье: и то и другое обесценивает монитор.
"""
from __future__ import annotations

import agent_center


def _seed(key: str, value, age_s: float, measured: bool = True) -> None:
    import time
    with agent_center._PROBES_LOCK:
        agent_center._PROBES[key] = {
            "value": value,
            "at": time.monotonic() - age_s,
            "running": True,        # чтобы тест не запускал настоящую пробу в сеть
            "measured": measured,
        }


def _cleanup(key: str) -> None:
    with agent_center._PROBES_LOCK:
        agent_center._PROBES.pop(key, None)


def test_fresh_measurement_is_returned():
    _seed("t-fresh", 123, age_s=10)
    try:
        assert agent_center._probe_value("t-fresh", 60, lambda: 999) == 123
    finally:
        _cleanup("t-fresh")


def test_measurement_within_the_grace_window_is_still_returned():
    """Небольшое отставание — норма: проба обновляется в фоне, а не в запросе."""
    _seed("t-grace", 123, age_s=150)  # 60 * 3 = 180 — ещё внутри окна
    try:
        assert agent_center._probe_value("t-grace", 60, lambda: 999) == 123
    finally:
        _cleanup("t-grace")


def test_stale_measurement_is_not_passed_off_as_current():
    """Именно это и показывало «не отвечает»: кэш вчерашнего сбоя."""
    _seed("t-stale", 123, age_s=20 * 3600)
    try:
        assert agent_center._probe_value("t-stale", 60, lambda: 999) is None
    finally:
        _cleanup("t-stale")


def test_stale_failure_is_not_passed_off_as_current_either():
    """Симметрично: суточный ноль тоже не описывает сегодняшнее состояние."""
    _seed("t-stale-fail", None, age_s=20 * 3600)
    try:
        assert agent_center._probe_value("t-stale-fail", 60, lambda: 999) is None
        assert agent_center._probe_measured("t-stale-fail", 60) is False
    finally:
        _cleanup("t-stale-fail")


def test_measured_flag_is_age_aware():
    """От этого флага зависит выбор между «проверяется» и «не отвечает» на странице."""
    _seed("t-age", 5, age_s=10)
    try:
        assert agent_center._probe_measured("t-age", 60) is True
    finally:
        _cleanup("t-age")

    _seed("t-age", 5, age_s=10 * 3600)
    try:
        assert agent_center._probe_measured("t-age", 60) is False
    finally:
        _cleanup("t-age")


def test_never_measured_is_not_current():
    _seed("t-none", None, age_s=0, measured=False)
    try:
        assert agent_center._probe_measured("t-none", 60) is False
    finally:
        _cleanup("t-none")


def test_zoom_unknown_is_not_rendered_as_a_failure():
    """None означает «нет актуального измерения», а не «токен не выдаётся».

    Прежний код писал ('токен ok' if zoom_ok else 'токен не выдаётся') — None ложен,
    поэтому отсутствие данных печаталось как отказ сервиса.
    """
    from pathlib import Path
    source = Path("agent_center.py").read_text(encoding="utf-8")
    assert "if zoom_ok is None:" in source, (
        "отсутствие измерения обязано вести в «проверяется», а не в ветку отказа"
    )


def test_probe_ttls_are_shared_constants():
    """Срок жизни и проверка актуальности не должны разъезжаться — это и был дефект."""
    assert agent_center._PROBE_TTL_BITRIX_S > 0
    assert agent_center._PROBE_TTL_ZOOM_S > 0
    from pathlib import Path
    source = Path("agent_center.py").read_text(encoding="utf-8")
    assert '_probe_value("bitrix", _PROBE_TTL_BITRIX_S' in source
    assert '_probe_measured("bitrix", _PROBE_TTL_BITRIX_S)' in source
