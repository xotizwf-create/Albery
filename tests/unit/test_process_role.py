"""Роль процесса: фоновые расписания крутятся РОВНО в одном месте.

Система разделяется на три службы из одного кода — бот, веб и MCP. Разделение именно по
ролям, а не на N одинаковых воркеров: накопление сообщений, реестр живых ходов для кнопки
«Новая сессия» и восстановление прерванных ходов — состояние в памяти, и все три приходят
через один вебхук /bitrix/imbot/. Пока роль бота однопроцессная, они работают как есть.

Опасность разделения ровно одна и она тихая: фоновое расписание, запущенное в каждой службе,
даст двойные и тройные уведомления живым людям (обход чекина пишет сотрудникам, автоматизации
агентов рассылают отчёты). Ошибка в одной переменной unit-файла — и человек получает три
одинаковых сообщения. Поэтому переключатель ОДИН, а тесты ниже закрепляют его поведение.
"""
from __future__ import annotations

import importlib

import pytest

import shared.role as role


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ALBERY_ROLE", raising=False)
    yield


def test_default_role_is_bot_so_existing_deploy_is_unchanged(monkeypatch):
    """Без ALBERY_ROLE поведение обязано быть ровно прежним — одна служба делает всё.

    Иначе выкладка кода до правки unit-файлов молча выключила бы все расписания.
    """
    assert role.current_role() == role.ROLE_BOT
    assert role.background_jobs_enabled() is True


def test_web_and_mcp_do_not_run_background_jobs(monkeypatch):
    for value in (role.ROLE_WEB, role.ROLE_MCP):
        monkeypatch.setenv("ALBERY_ROLE", value)
        assert role.current_role() == value
        assert role.background_jobs_enabled() is False, f"{value} не должен крутить расписания"


def test_bot_role_runs_background_jobs(monkeypatch):
    monkeypatch.setenv("ALBERY_ROLE", role.ROLE_BOT)
    assert role.background_jobs_enabled() is True


def test_typo_in_unit_file_falls_back_to_bot_not_to_nothing(monkeypatch):
    """Опечатка не должна оставить систему БЕЗ планировщиков.

    Лишний прогон в известном месте лечится; молчаливое отсутствие расписаний обнаружится
    через сутки по невыставленным задачам — это хуже.
    """
    for typo in ("bott", "wbe", "", "   ", "worker", "mcp-server"):
        monkeypatch.setenv("ALBERY_ROLE", typo)
        assert role.background_jobs_enabled() is True, f"{typo!r} обязан вести себя как bot"


def test_role_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("ALBERY_ROLE", "WEB")
    assert role.current_role() == role.ROLE_WEB
    monkeypatch.setenv("ALBERY_ROLE", "  Mcp  ")
    assert role.current_role() == role.ROLE_MCP


BACKGROUND_MODULES = (
    ("agent_automations", "автоматизации агентов"),
    ("agent_center", "сторож здоровья"),
    ("recurring_scheduler", "повторяющиеся задачи"),
    ("task_checkin", "обход чекина по задачам"),
    ("b24bot", "обход простаивающих сессий"),
)


@pytest.mark.parametrize("module_name, what", BACKGROUND_MODULES)
def test_every_background_loop_is_behind_the_role_guard(module_name, what):
    """Каждый фоновый цикл обязан быть за ролевой защитой, а не только за своим флагом.

    Проверка идёт по исходнику намеренно: импортировать эти модули с ролью web и смотреть,
    что потоки не поднялись, значило бы поднимать их в тесте. Забытая защита — это двойные
    уведомления людям, поэтому её отсутствие должно ловиться механически, а не на ревью.
    """
    import pathlib

    source = pathlib.Path(f"{module_name}.py").read_text(encoding="utf-8")
    starts = [ln for ln in source.splitlines()
              if ln.startswith("if os.getenv(") and "threading" not in ln]
    guarded = [ln for ln in starts if "background_jobs_enabled()" in ln]
    assert guarded, f"{module_name} ({what}): фоновый цикл без ролевой защиты"
