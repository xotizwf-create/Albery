from datetime import datetime

import pytest

from shared.agent_channel_runtime import ChannelContext, build_agent_policy, load_profile_knowledge


PROFILE = {
    "slug": "sales",
    "name": "Продажи",
    "role_prompt": "Консультант по продажам",
}


def _policy(channel: str, bitrix_user_id=None) -> str:
    return build_agent_policy(
        PROFILE,
        ChannelContext(
            channel=channel,
            conversation_id="chat-17",
            requester_name="Анна",
            requester_platform_id="501",
            requester_bitrix_user_id=bitrix_user_id,
        ),
        core_instructions="Общее правило",
        selected_skills=[{"title": "Продажи", "content": "Полный навык"}],
        personal_instructions=[{"name": "Тон", "content": "Отвечай спокойно"}],
        now=datetime(2026, 8, 11, 12, 30),
    )


def test_profile_behaviour_and_knowledge_are_identical_across_channels():
    bitrix = _policy("bitrix", 17)
    telegram = _policy("telegram", 17)

    for shared_fact in (
        "профиль `sales`",
        "Консультант по продажам",
        "Общее правило",
        "Полный навык",
        "Отвечай спокойно",
        "schedule_my_automation",
        "Bitrix user id=17",
    ):
        assert shared_fact in bitrix
        assert shared_fact in telegram

    assert "delivery_channel='bitrix'" in bitrix
    assert "delivery_channel='telegram'" in telegram
    assert "Bitrix24" in bitrix and "[b]...[/b]" in bitrix
    assert "Telegram" in telegram and "без Bitrix BBCode" in telegram


def test_unmapped_telegram_user_cannot_be_impersonated_in_bitrix():
    policy = _policy("telegram")

    assert "Связь с сотрудником Bitrix не подтверждена" in policy
    assert "не выполняй действие от лица человека" in policy


def test_channel_context_is_typed_and_scoped():
    ctx = ChannelContext(channel="telegram", conversation_id="501")

    assert ctx.scope == "telegram:501"
    assert ctx.automation_destination == {
        "delivery_channel": "telegram",
        "delivery_conversation_id": "501",
    }

    with pytest.raises(ValueError):
        ChannelContext(channel="email", conversation_id="501")
    with pytest.raises(ValueError):
        ChannelContext(channel="telegram", conversation_id="")


def test_main_profile_does_not_duplicate_the_gateway_skill_catalog(monkeypatch):
    import agent_knowledge

    monkeypatch.setattr(agent_knowledge, "load_instructions", lambda: [])
    monkeypatch.setattr(agent_knowledge, "load_manifest", lambda _slug: {"skills": ["skill:huge"]})
    monkeypatch.setattr(agent_knowledge, "load_skills", lambda: [{
        "id": "skill:huge", "title": "Huge", "description": "Gateway-owned", "custom": True,
    }])
    monkeypatch.setattr(agent_knowledge, "load_skill_content", lambda _skill: "x" * 100_000)
    monkeypatch.setattr(agent_knowledge, "load_agent_learned", lambda _slug: [])

    knowledge = load_profile_knowledge("main")

    assert knowledge["selected_skills"] == []


def test_subagent_gets_only_full_bodies_for_custom_selected_skills(monkeypatch):
    import agent_knowledge

    monkeypatch.setattr(agent_knowledge, "load_instructions", lambda: [])
    monkeypatch.setattr(agent_knowledge, "load_manifest", lambda _slug: {
        "skills": ["skill:custom", "skill:bundled"]
    })
    monkeypatch.setattr(agent_knowledge, "load_skills", lambda: [
        {"id": "skill:custom", "title": "Custom", "description": "C", "custom": True},
        {"id": "skill:bundled", "title": "Bundled", "description": "B", "custom": False},
    ])
    monkeypatch.setattr(agent_knowledge, "load_skill_content", lambda skill: "body:" + skill)
    monkeypatch.setattr(agent_knowledge, "load_agent_learned", lambda _slug: [])

    selected = load_profile_knowledge("sales")["selected_skills"]

    assert selected[0]["content"] == "body:skill:custom"
    assert "content" not in selected[1]
