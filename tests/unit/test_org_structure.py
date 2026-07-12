"""Org-structure tools: only Евгений(14) / ИИ Агент(22) may change it, and only with confirm."""
from __future__ import annotations

import pytest


def test_only_allowlisted_users_may_change(ctx, monkeypatch):
    monkeypatch.delenv("ORG_STRUCTURE_ADMINS", raising=False)
    monkeypatch.setattr(ctx, "_resolve_active_bitrix_user",
                        lambda uid, name: {"bitrix_user_id": uid, "full_name": "Кто-то"})
    # Александр (16) — админ по тирам, но оргструктуру менять НЕ вправе
    with pytest.raises(ctx.McpError) as exc:
        ctx._org_assert_allowed({"requested_by_bitrix_user_id": 16, "confirm": True}, "тест")
    assert "нет прав" in str(exc.value)
    # разрешённые проходят
    assert ctx._org_assert_allowed({"requested_by_bitrix_user_id": 14, "confirm": True}, "тест") == 14
    assert ctx._org_assert_allowed({"requested_by_bitrix_user_id": 22, "confirm": True}, "тест") == 22


def test_requester_is_mandatory(ctx):
    with pytest.raises(ctx.McpError) as exc:
        ctx._org_assert_allowed({"confirm": True}, "тест")
    assert "requested_by_bitrix_user_id" in str(exc.value)


def test_confirm_gate(ctx):
    with pytest.raises(ctx.McpError) as exc:
        ctx._org_assert_allowed({"requested_by_bitrix_user_id": 14}, "отдел: create")
    assert "confirm=true" in str(exc.value)


def test_allowlist_is_env_tunable(ctx, monkeypatch):
    monkeypatch.setenv("ORG_STRUCTURE_ADMINS", "14, 22, 99")
    assert ctx._org_admin_ids() == {14, 22, 99}


def test_org_tools_registered_and_not_on_faq(ctx):
    required = {"get_bitrix_departments", "manage_bitrix_department", "assign_employee_department"}
    assert required <= set(ctx.TOOLS)
    # приватные данные компании — не на публичном FAQ-тире
    assert not (required & set(ctx.FAQ_TOOL_NAMES))


def test_root_department_protected(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "_org_webhook",
                        lambda m, p=None: [{"ID": "1", "NAME": "Битрикс"}] if m == "department.get" else True)
    with pytest.raises(ctx.McpError) as exc:
        ctx.tool_manage_bitrix_department({"action": "delete", "department_id": 1,
                                           "requested_by_bitrix_user_id": 14, "confirm": True})
    assert "Корневой" in str(exc.value)
