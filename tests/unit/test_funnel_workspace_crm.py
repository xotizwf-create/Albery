from __future__ import annotations

from copy import deepcopy

import pytest

import funnel_workspace_crm as crm
import funnel_workspace_store as store
import tg_agent


def conversation(**overrides):
    row = {
        "id": 7,
        "source_key": "telegram",
        "external_user_id": 987654321,
        "external_chat_id": "987654321",
        "business_connection_id": "business-1",
        "username": "client_user",
        "display_name": "Иван Клиентов",
        "deal_id": None,
        "state_version": 3,
    }
    row.update(overrides)
    return row


class FakeWorkspace:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.update_calls = []
        self.conflicts = 0

    def get(self, conversation_id):
        assert conversation_id == self.row["id"]
        return deepcopy(self.row)

    def update(self, conversation_id, **kwargs):
        assert conversation_id == self.row["id"]
        self.update_calls.append(deepcopy(kwargs))
        if self.conflicts:
            self.conflicts -= 1
            self.row["state_version"] += 1
            raise store.WorkspaceConflictError("changed")
        assert kwargs["expected_version"] == self.row["state_version"]
        self.row.update(
            deal_id=kwargs["deal_id"],
            funnel_id=kwargs["funnel_id"],
            stage_id=kwargs["stage_id"],
            state_version=self.row["state_version"] + 1,
        )
        return deepcopy(self.row)


def test_existing_local_deal_is_primary_dedup_barrier():
    workspace = FakeWorkspace(conversation(deal_id=82))
    calls = []

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=lambda tool, args: calls.append((tool, args)),
    )

    assert result["deal_id"] == 82
    assert result["status"] == "already_linked"
    assert result["already_linked"] is True
    assert calls == []
    assert workspace.update_calls == []


def test_user_without_username_never_corrupts_legacy_username_field():
    workspace = FakeWorkspace(conversation(username=None, display_name=None))
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "list_crm_deals":
            return {"deals": []}
        if tool == "create_crm_deal":
            return {"deal_id": 500}
        raise AssertionError(tool)

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
        telegram_id_field="UF_CRM_WORKSPACE_TG_ID",
    )

    assert result["status"] == "created"
    assert result["deal_id"] == 500
    create_args = [args for tool, args in calls if tool == "create_crm_deal"][0]
    assert create_args["category_id"] == 16
    assert create_args["stage"] == tg_agent.STAGE_NEW
    assert create_args["custom_fields"] == {
        "UF_CRM_WORKSPACE_TG_ID": "987654321",
    }
    assert tg_agent.CRM_TELEGRAM_FIELD not in create_args["custom_fields"]
    assert "[tg:987654321]" in create_args["title"]
    assert "ID 987654321" in create_args["title"]
    assert "Telegram user id: 987654321;" in create_args["comments"]
    assert "[tg:987654321]" in create_args["comments"]
    assert workspace.update_calls == [
        {
            "deal_id": 500,
            "funnel_id": 16,
            "stage_id": tg_agent.STAGE_NEW,
            "expected_version": 3,
        }
    ]


def test_username_stays_in_legacy_field_and_numeric_id_uses_separate_field():
    payload = crm.build_deal_payload(
        conversation(),
        stage_id=tg_agent.STAGE_NEW,
        telegram_field=tg_agent.CRM_TELEGRAM_FIELD,
        telegram_id_field="UF_CRM_WORKSPACE_TG_ID",
    )

    assert payload["custom_fields"] == {
        tg_agent.CRM_TELEGRAM_FIELD: "client_user",
        "UF_CRM_WORKSPACE_TG_ID": "987654321",
    }
    assert payload["custom_fields"][tg_agent.CRM_TELEGRAM_FIELD] != "987654321"


def test_recovers_deal_after_crm_create_without_local_commit():
    workspace = FakeWorkspace(conversation())
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        assert tool == "list_crm_deals"
        return {
            "deals": [
                {
                    "deal_id": 501,
                    "category_id": 16,
                    "title": "Лид Telegram [tg:987654321] — Иван",
                    "custom_fields": {
                        tg_agent.CRM_TELEGRAM_FIELD: "client_user",
                    },
                }
            ]
        }

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["status"] == "recovered"
    assert result["deal_id"] == 501
    assert [tool for tool, _ in calls] == ["list_crm_deals"]
    assert workspace.row["deal_id"] == 501


def test_edited_title_is_recovered_by_dedicated_numeric_id_field():
    workspace = FakeWorkspace(conversation())
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        assert tool == "list_crm_deals"
        return {
            "total": 1,
            "deals": [
                {
                    "deal_id": 502,
                    "category_id": 16,
                    "title": "ИУ — Иван, название изменено оператором",
                    "custom_fields": {
                        tg_agent.CRM_TELEGRAM_FIELD: "client_user",
                        "UF_CRM_WORKSPACE_TG_ID": "987654321",
                    },
                }
            ],
        }

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
        telegram_id_field="UF_CRM_WORKSPACE_TG_ID",
    )

    assert result["status"] == "recovered"
    assert result["deal_id"] == 502
    assert [tool for tool, _ in calls] == ["list_crm_deals"]
    list_args = calls[0][1]
    assert "search" not in list_args
    assert list_args["include_custom_fields"] is True


def test_does_not_match_numeric_prefix_or_wrong_category():
    row = conversation(external_user_id=123)
    workspace = FakeWorkspace(row)
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "list_crm_deals":
            return {
                "deals": [
                    {
                        "deal_id": 10,
                        "category_id": 16,
                        "title": "Лид Telegram [tg:1234]",
                        "custom_fields": {
                            tg_agent.CRM_TELEGRAM_FIELD: "1234",
                        },
                    },
                    {
                        "deal_id": 11,
                        "category_id": 99,
                        "title": "Лид Telegram [tg:123]",
                        "custom_fields": {
                            tg_agent.CRM_TELEGRAM_FIELD: "123",
                        },
                    },
                ]
            }
        return {"deal_id": 12}

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 12
    assert result["created"] is True
    assert [tool for tool, _ in calls] == ["list_crm_deals", "create_crm_deal"]


def test_same_username_and_identity_field_is_rejected_before_crm_call():
    workspace = FakeWorkspace(conversation())
    calls = []

    with pytest.raises(crm.WorkspaceCrmError, match="must differ"):
        crm.ensure_conversation_deal(
            7,
            get_conversation=workspace.get,
            update_crm_link=workspace.update,
            crm_call=lambda tool, args: calls.append((tool, args)),
            telegram_field="UF_CRM_TELEGRAM",
            telegram_id_field="uf_crm_telegram",
        )

    assert calls == []


def test_missing_numeric_identity_never_calls_crm():
    workspace = FakeWorkspace(conversation(external_user_id=None, username="display_only"))
    calls = []

    with pytest.raises(crm.TelegramIdentityError):
        crm.ensure_conversation_deal(
            7,
            get_conversation=workspace.get,
            update_crm_link=workspace.update,
            crm_call=lambda tool, args: calls.append((tool, args)),
        )

    assert calls == []
    assert workspace.update_calls == []


def test_concurrent_local_link_wins_over_just_created_deal():
    workspace = FakeWorkspace(conversation())

    def crm_call(tool, _args):
        if tool == "list_crm_deals":
            return {"deals": []}
        if tool == "create_crm_deal":
            return {"deal_id": 700}
        raise AssertionError(tool)

    def concurrent_update(conversation_id, **kwargs):
        assert conversation_id == 7
        workspace.update_calls.append(deepcopy(kwargs))
        workspace.row.update(
            deal_id=701,
            funnel_id=16,
            stage_id=tg_agent.STAGE_NEW,
        )
        return deepcopy(workspace.row)

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=concurrent_update,
        crm_call=crm_call,
    )

    assert result["status"] == "concurrent_link"
    assert result["deal_id"] == 701
    assert result["orphan_deal_id"] == 700


def test_retries_only_local_link_on_state_version_conflict():
    workspace = FakeWorkspace(conversation())
    workspace.conflicts = 1

    def crm_call(tool, _args):
        if tool == "list_crm_deals":
            return {"deals": []}
        return {"deal_id": 700}

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 700
    assert len(workspace.update_calls) == 2
    assert workspace.update_calls[0]["expected_version"] == 3
    assert workspace.update_calls[1]["expected_version"] == 4


def test_malformed_create_response_is_retryable_via_marker():
    workspace = FakeWorkspace(conversation())

    def crm_call(tool, _args):
        if tool == "list_crm_deals":
            return {"deals": []}
        return {"created": True}

    with pytest.raises(crm.CrmResponseError, match="without a positive deal id"):
        crm.ensure_conversation_deal(
            7,
            get_conversation=workspace.get,
            update_crm_link=workspace.update,
            crm_call=crm_call,
        )

    assert workspace.update_calls == []


def test_reconciliation_failure_fails_closed_without_creating():
    workspace = FakeWorkspace(conversation())
    calls = []

    def crm_call(tool, args):
        calls.append((tool, args))
        raise RuntimeError("CRM unavailable")

    with pytest.raises(RuntimeError, match="CRM unavailable"):
        crm.ensure_conversation_deal(
            7,
            get_conversation=workspace.get,
            update_crm_link=workspace.update,
            crm_call=crm_call,
        )

    assert [tool for tool, _ in calls] == ["list_crm_deals"]
    assert workspace.update_calls == []


def test_stage_action_skips_second_mutation_when_target_is_already_set():
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        assert tool == "get_crm_deal"
        return {"deal_id": 82, "stage_id": "C16:TERMS"}

    result = crm.apply_conversation_stage_action(
        7,
        "C16:TERMS",
        crm_call=crm_call,
        ensure_deal=lambda _conversation_id: {
            "deal_id": 82,
            "status": "already_linked",
        },
    )

    assert result["status"] == "already_applied"
    assert result["deal_id"] == 82
    assert [tool for tool, _args in calls] == ["get_crm_deal"]


def test_stage_action_sets_desired_stage_and_verifies_result():
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "get_crm_deal":
            return {"deal": {"deal_id": 82, "stage_id": "C16:NEW"}}
        if tool == "update_crm_deal":
            return {"updated": True, "deal_id": 82, "stage_id": "C16:TERMS"}
        raise AssertionError(tool)

    result = crm.apply_conversation_stage_action(
        7,
        "C16:TERMS",
        crm_call=crm_call,
        ensure_deal=lambda _conversation_id: {
            "deal_id": 82,
            "status": "already_linked",
        },
    )

    assert result["status"] == "applied"
    assert result["previous_stage"] == "C16:NEW"
    assert calls == [
        ("get_crm_deal", {"deal_id": 82}),
        ("update_crm_deal", {"deal_id": 82, "stage": "C16:TERMS"}),
    ]


def test_stage_action_rechecks_after_adapter_returns_no_stage():
    stages = iter(("C16:NEW", "C16:TERMS"))
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "get_crm_deal":
            return {"stage_id": next(stages)}
        if tool == "update_crm_deal":
            return {"updated": True}
        raise AssertionError(tool)

    result = crm.apply_conversation_stage_action(
        7,
        "C16:TERMS",
        crm_call=crm_call,
        ensure_deal=lambda _conversation_id: {"deal_id": 82, "status": "linked"},
    )

    assert result["status"] == "applied"
    assert [tool for tool, _args in calls] == [
        "get_crm_deal",
        "update_crm_deal",
        "get_crm_deal",
    ]


def test_person_already_in_the_funnel_never_gets_a_second_deal():
    """Живой дубль 27.07.2026: у Дарьи Фартаковой в воронке 16 уже лежала сделка #226
    «Fartakova_D - Открытая линия», а рабочее пространство минутой позже завело ей
    вторую (#228). Маркера `[tg:…]` на чужой сделке нет — узнаём человека по username."""
    workspace = FakeWorkspace(
        conversation(external_user_id=609797080, username="Fartakova_D",
                     display_name="Дарья Фартакова")
    )
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "list_crm_deals":
            return {
                "total": 1,
                "deals": [
                    {
                        "deal_id": 226,
                        "category_id": 16,
                        "title": "Fartakova_D - Открытая линия",
                        "custom_fields": {},
                    }
                ],
            }
        raise AssertionError(f"новая сделка заводиться не должна: {tool}")

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["status"] == "recovered"
    assert result["deal_id"] == 226
    assert workspace.row["deal_id"] == 226
    assert [tool for tool, _ in calls] == ["list_crm_deals"]


def test_old_telegram_lead_written_with_an_at_sign_is_recognised():
    """Сделки прошлого формата несут `@username` — их тоже нельзя дублировать."""
    workspace = FakeWorkspace(conversation(username="client_user"))

    def crm_call(tool, args):
        if tool == "list_crm_deals":
            return {
                "total": 1,
                "deals": [
                    {
                        "deal_id": 140,
                        "category_id": 16,
                        "title": "Лид Telegram @client_user — Иван Клиентов",
                        "custom_fields": {},
                    }
                ],
            }
        raise AssertionError(f"новая сделка заводиться не должна: {tool}")

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 140
    assert result["status"] == "recovered"


def test_the_oldest_foreign_card_wins_so_the_client_keeps_one_deal():
    """Если чужих карточек по username уже несколько, диалог цепляется к САМОЙ РАННЕЙ:
    в ней история, а поздние — как раз те дубли, ради которых всё это и делается."""
    workspace = FakeWorkspace(conversation(username="client_user"))

    def crm_call(tool, args):
        if tool == "list_crm_deals":
            return {
                "total": 2,
                "deals": [
                    {"deal_id": 300, "category_id": 16,
                     "title": "client_user — повторное обращение", "custom_fields": {}},
                    {"deal_id": 140, "category_id": 16,
                     "title": "client_user - Открытая линия", "custom_fields": {}},
                ],
            }
        raise AssertionError(f"новая сделка заводиться не должна: {tool}")

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 140


def test_our_own_marked_deal_beats_a_username_lookalike():
    """Маркер `[tg:<id>]` опознаёт человека наверняка, username — лишь вероятно: его
    меняют, и он может достаться другому. Точное совпадение всегда сильнее."""
    workspace = FakeWorkspace(conversation(username="client_user"))

    def crm_call(tool, args):
        if tool == "list_crm_deals":
            return {
                "total": 2,
                "deals": [
                    {"deal_id": 300, "category_id": 16,
                     "title": "Лид Telegram [tg:987654321] — Иван", "custom_fields": {}},
                    {"deal_id": 140, "category_id": 16,
                     "title": "client_user - Открытая линия", "custom_fields": {}},
                ],
            }
        raise AssertionError(f"новая сделка заводиться не должна: {tool}")

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 300


def test_username_inside_a_longer_word_is_not_a_match():
    """`@ivan` не должен цепляться к сделке «ivanov»: чужая карточка страшнее дубля."""
    workspace = FakeWorkspace(conversation(username="ivan"))
    calls = []

    def crm_call(tool, args):
        calls.append((tool, deepcopy(args)))
        if tool == "list_crm_deals":
            return {
                "total": 1,
                "deals": [
                    {"deal_id": 400, "category_id": 16,
                     "title": "ivanov - Открытая линия", "custom_fields": {}},
                ],
            }
        return {"deal_id": 401}

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 401
    assert result["created"] is True


def test_username_match_ignores_deals_of_another_funnel():
    """Одноимённая сделка в другой воронке — не «человек уже в воронке ИУ»."""
    workspace = FakeWorkspace(conversation(username="client_user"))

    def crm_call(tool, args):
        if tool == "list_crm_deals":
            return {
                "total": 1,
                "deals": [
                    {"deal_id": 500, "category_id": 0,
                     "title": "client_user - Открытая линия", "custom_fields": {}},
                ],
            }
        return {"deal_id": 501}

    result = crm.ensure_conversation_deal(
        7,
        get_conversation=workspace.get,
        update_crm_link=workspace.update,
        crm_call=crm_call,
    )

    assert result["deal_id"] == 501
    assert result["created"] is True
