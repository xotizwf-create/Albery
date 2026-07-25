"""Fresh-database migration dependency guards."""
from scripts import ensure_postgres


def test_system_automation_keys_run_after_agent_automations_table():
    migrations = ensure_postgres.ALWAYS_APPLY_MIGRATIONS

    assert migrations.index("041_agent_automations.sql") < migrations.index(
        "057_system_automation_keys.sql"
    )
