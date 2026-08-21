"""Мастер подключения: человек подключает свой аккаунт Авито сам, без разработчика.

За человеком остаётся только то, что за него не может сделать никто: пройти капчу и
ввести код из SMS со своего телефона. Всё остальное — код аккаунта, запись в Albery,
включение после входа — делает мастер.

Ключевой инвариант: аккаунт включается ТОЛЬКО после подтверждённого входа. Включить его
заранее значит отдать воркеру аккаунт без сессии — сторож здоровья немедленно поднимет
тревогу о мёртвом выходе, которого ещё и не заводили.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "avito_worker.py"


@pytest.fixture(scope="module")
def worker():
    spec = importlib.util.spec_from_file_location("avito_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("label,expected", [
    ("Рабочий", "rabochiy"),
    ("Отдел закупок", "otdel-zakupok"),
    ("Avito Shop 2", "avito-shop-2"),
    ("  Склад  №1  ", "sklad-1"),
])
def test_the_code_is_derived_from_the_name(worker, label, expected):
    """Человек называет аккаунт словами; латинский код — забота мастера, не его."""
    assert worker._slug_from_label(label) == expected


def test_a_name_without_letters_still_gives_a_usable_code(worker):
    """Пустой код уронил бы запись в Albery — на такой случай есть запасной."""
    slug = worker._slug_from_label("!!!")

    assert slug and slug.startswith("avito-")


def test_the_code_never_breaks_the_server_rule(worker):
    """На сервере код проверяется регулярным выражением — мастер обязан ему соответствовать."""
    import re

    server_rule = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
    for label in ("Рабочий", "Отдел закупок", "  Склад  №1  ", "!!!", "Ёлка-2"):
        assert server_rule.match(worker._slug_from_label(label)), label
