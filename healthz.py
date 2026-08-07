"""Адрес проверки живости, который РЕАЛЬНО ходит в базу.

Появился после разбора 07.08.2026. Роли web и mcp были выложены с флагом gunicorn --preload;
приложение обращается к базе уже на импорте, поэтому пул psycopg успевал создаться в мастере
до раздвоения на воркеров и не переживал его — каждый запрос падал через 30 секунд с
PoolTimeout, и Центр Агента перестал открываться.

Отдельно скверно то, что проверка после выкладки этого НЕ ПОКАЗАЛА: я смотрел, что порт
слушает, /login отдаёт 200, а /mcp — 401. Оба эти адреса до базы не доходят, поэтому служба
выглядела здоровой ровно до первого обращения к данным.

Отсюда правило: у каждой роли обязан быть адрес, проверяющий связь с базой, и деплой обязан
его дёргать. Аутентификации здесь нет намеренно — иначе проверку не выполнить из скрипта, —
но и наружу не отдаётся ничего, кроме «база отвечает» и роли процесса.
"""
from __future__ import annotations

import logging

from flask import jsonify

from app import app, pg_connect
from shared.role import current_role


@app.get("/healthz")
def healthz():
    """200 — процесс жив И умеет получить соединение с базой. 503 — не умеет."""
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logging.warning("healthz: база недоступна", exc_info=True)
        return jsonify({
            "status": "degraded",
            "role": current_role(),
            "database": "unreachable",
            "detail": type(exc).__name__,
        }), 503
    return jsonify({"status": "ok", "role": current_role(), "database": "ok"})


logging.getLogger(__name__).info("healthz loaded: /healthz registered")
