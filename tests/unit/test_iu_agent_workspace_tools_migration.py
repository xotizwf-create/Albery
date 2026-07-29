from pathlib import Path


def test_staff_iu_agent_keeps_workspace_control_and_status_tools():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "078_iu_agent_workspace_tools.sql"
    ).read_text(encoding="utf-8")

    assert "agent-po-rabote-s-iu" in migration
    assert "workspace_set_control" in migration
    assert "workspace_set_status" in migration
    assert "tools" in migration
    assert "|| ARRAY[" in migration
    assert "tools_customized" not in migration
