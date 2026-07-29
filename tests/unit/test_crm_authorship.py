"""Сделки воронки ведёт «ИИ Агент», а не тот, чей токен оказался свежим.

Владелец 29.07.2026: «все сделки должны создаваться, заводиться и изменяться от имени ИИ
агента; сейчас при смене стадии — Дмитрий Строганов, при создании — Софья, при анкете —
Евгений».

Корень: токен приложения хранится ОДИН на всю систему и перезаписывается тем, кто последним
вызвал событие портала, — поэтому авторство доставалось случайному человеку. Живой снимок
воронки ИУ 29.07.2026: сделку 274 создал 36 (Софья), 240 — 94 (Юлия), 238 — 14 (Евгений),
230 — 28 (Артур), а более ранние 228/222/216/214 — 22 (ИИ Агент).

Вебхуки портала при этом принадлежат пользователю 22 и подписывают свои вызовы им всегда.
"""
from __future__ import annotations

import funnel_workspace_crm as crm

CONVERSATION = {
    "id": 241,
    "external_user_id": 1451982360,
    "username": "alexxandrn",
    "display_name": "Александр Никитенко",
}


def test_new_deal_belongs_to_the_ai_agent():
    payload = crm.build_deal_payload(
        CONVERSATION, stage_id="C16:NEW", telegram_field="UF_CRM_1784296997")

    assert payload["responsible_bitrix_user_id"] == crm.AGENT_USER_ID
    assert crm.AGENT_USER_ID == 22


def test_agent_user_is_configurable_without_deploy(monkeypatch):
    """Пользователя могут пересоздать — тогда правится настройка, а не код."""
    monkeypatch.setenv("CRM_AGENT_USER_ID", "77")
    import importlib

    reloaded = importlib.reload(crm)
    try:
        assert reloaded.AGENT_USER_ID == 77
    finally:
        monkeypatch.delenv("CRM_AGENT_USER_ID", raising=False)
        importlib.reload(crm)


def test_marker_and_title_are_unchanged_by_the_authorship_fix():
    """Правка авторства не должна задеть то, чем склеиваются карточки."""
    payload = crm.build_deal_payload(
        CONVERSATION, stage_id="C16:NEW", telegram_field="UF_CRM_1784296997")

    assert "[tg:1451982360]" in payload["title"]
    assert payload["custom_fields"]["UF_CRM_1784296997"] == "alexxandrn"
