from __future__ import annotations


def test_all_versioned_agent_manifests_have_explicit_tool_caps():
    from pathlib import Path

    import yaml

    agents_dir = Path(__file__).resolve().parents[2] / "agent_knowledge" / "agents"
    for path in sorted(agents_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert isinstance(payload.get("tools"), list), path.name


def test_operational_caps_freeze_current_reviewed_registry():
    from agent_knowledge import load_manifest
    from mcp.tool_policy import REVIEWED_TOOL_NAMES

    operational = {
        "agent-finansist",
        "agent-po-rabote-s-iu",
        "agent-razrabotchik",
        "agent-sklad",
        "main",
        "menedzher-marketpleysa",
        "novostnoy-agent",
    }
    for slug in operational:
        assert set(load_manifest(slug)["tools"]) == set(REVIEWED_TOOL_NAMES), slug

    for slug in {"albery-ai-bot", "iu-customer-runtime"}:
        assert load_manifest(slug)["tools"] == []


def test_missing_manifest_cannot_receive_base_or_max_tools():
    import agent_center

    for mode in ("base", "custom", "max"):
        agent = {
            "slug": "profile-without-a-versioned-manifest",
            "tools_mode": mode,
            "tools": ["search_tasks"],
            "tools_customized": True,
        }
        assert agent_center._agent_tool_names(agent) == set()
        assert agent_center._agent_self_tool_names(agent) == set()


def test_inventory_covers_every_reviewed_tool():
    from mcp.tool_policy import REVIEWED_TOOL_NAMES
    from scripts.audit_mcp_capabilities import rows

    inventory = rows()
    assert len(inventory) == 167
    assert {item["name"] for item in inventory} == set(REVIEWED_TOOL_NAMES)


def test_new_agent_creation_writes_cap_before_connectors():
    import inspect

    import agent_center

    source = inspect.getsource(agent_center.agent_center_create_agent)
    cap_write = source.index("save_manifest(slug, [], [], tools=sorted(REVIEWED_TOOL_NAMES))")
    bitrix_connector = source.index("_register_agent_bot")
    hermes_connector = source.index("_hermes_connector_add")
    assert cap_write < bitrix_connector
    assert cap_write < hermes_connector
    assert "is_active = FALSE" in source


def test_deploy_and_continuous_checks_watch_cap_drift():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    smoke = (root / "scripts" / "deploy_smoke.py").read_text(encoding="utf-8")
    selfcheck = (root / "scripts" / "albery_selfcheck.py").read_text(encoding="utf-8")
    for source in (smoke, selfcheck):
        assert "REVIEWED_TOOL_NAMES" in source
        assert "ZERO_TOOL_AGENT_SLUGS" in source
        assert "versioned MCP" in source or "versioned tool cap" in source
