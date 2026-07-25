"""Fresh-database migration dependency guards."""
from scripts import ensure_postgres


def test_system_automation_keys_run_after_agent_automations_table():
    migrations = ensure_postgres.ALWAYS_APPLY_MIGRATIONS

    assert migrations.index("041_agent_automations.sql") < migrations.index(
        "057_system_automation_keys.sql"
    )


def test_interaction_error_index_runs_after_agent_attribution_column():
    migrations = ensure_postgres.ALWAYS_APPLY_MIGRATIONS

    assert migrations.index("037_agents.sql") < migrations.index(
        "059_interaction_error_resolution.sql"
    )
