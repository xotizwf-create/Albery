"""The per-agent monitoring filter must map UI selections to SQL exactly:
'all' → everything, 'main' → the universal agent's rows (agent_slug IS NULL),
a slug → that subagent only. A wrong clause here silently mixes agents' numbers."""

import app  # noqa: F401 — must be imported before agent_center (circular-import rule)
import agent_center


def test_all_and_empty_mean_no_filter():
    assert agent_center._monitoring_agent_filter("all") == ("", {})
    assert agent_center._monitoring_agent_filter("") == ("", {})
    assert agent_center._monitoring_agent_filter(None) == ("", {})
    assert agent_center._monitoring_agent_filter("  ") == ("", {})


def test_main_filters_null_slug_without_params():
    sql, params = agent_center._monitoring_agent_filter("main")
    assert "agent_slug IS NULL" in sql
    assert sql.startswith(" AND ")
    assert params == {}


def test_subagent_slug_is_bound_as_parameter():
    sql, params = agent_center._monitoring_agent_filter("agent-sklad")
    assert "agent_slug = %(agent_slug)s" in sql
    assert params == {"agent_slug": "agent-sklad"}
