"""Versioned semantic safety policy for Albery's model-facing MCP tools.

The registry in :mod:`mcp.context_server` describes how to call a tool.  This module
describes what the call *means* for safety purposes.  Keeping the reviewed name set
here makes a newly registered tool fail closed until its effect and confirmation
requirements have been reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


REGULAR_TOOL_NAMES = frozenset(
    """
add_bitrix_task_comment add_deal_comment add_task_checklist add_task_reminder
assign_employee_department attach_files_to_task cancel_owner_recommendation
check_google_sheet_health check_mail_bounces
complete_bitrix_task convert_document create_agent create_bitrix_task create_crm_deal
create_crm_pipeline create_drive_folder create_google_doc create_google_sheet
create_recurring_task create_telegram_agent delete_agent delete_bitrix_task
delete_crm_deal delete_crm_pipeline delete_dialog delete_recurring_task
delete_telegram_agent delete_zoom_call_report dispatch_leader_evaluations_digest
dispatch_owner_weekly_report_task dispatch_zoom_operational_tasks
dispatch_zoom_participant_reports edit_attachment_document edit_google_doc
export_document export_zoom_call_markdown export_zoom_transcripts_markdown fetch_url
format_google_sheet get_agent_decisions get_agent_link get_agent_monitoring
get_ai_capabilities get_ai_instructions get_attachment_text get_bitrix_bot_chat
get_bitrix_departments get_chat_ocr_status get_chat_transcript get_compact_export
get_company_file get_company_profile get_context_guide get_crm_deal
get_employee_absences get_employee_dossier get_google_sheet_meta get_latest_news_digest
get_org_structure get_owner_reports get_period_index get_previous_owner_daily_context
get_recommendation_feedback_context get_report_contract get_report_readiness
get_runtime_status get_task_comments get_task_history get_telegram_dialog get_tg_news
get_wb_prices get_webapp_template get_zoom_call_transcript health join_telegram_chat
label_mail link_tasks list_agents list_available_sources list_bitrix_bot_sessions list_chats
list_company_files list_crm_deal_fields list_crm_deals list_crm_forms
list_crm_lead_contacts list_crm_pipelines list_dialog_errors list_drive_folder_items
list_drive_folders list_leader_evaluations list_overdue_tasks
list_pending_owner_recommendations list_pending_zoom_operational_dispatches
list_periods list_recommendations list_recurring_tasks list_task_userfields
list_telegram_agents list_telegram_chats list_telegram_contacts list_zoom_calls
log_task_time make_sheet_applet manage_apps_script manage_bitrix_department
manage_crm_deal_field manage_crm_pipeline_stage move_drive_file_to_folder
next_contract_number notify_client_when_task_done notify_iu_group organize_drive_folder
preview_zoom_operational_tasks preview_zoom_participant_reports process_chat_ocr
read_google_doc read_google_sheet_values read_mail read_mail_full read_mail_thread read_telegram_chat remove_drive_item_from_folder
reopen_bitrix_task report_overdue_discipline resolve_dialog_errors save_news_digest
save_owner_daily_report save_owner_weekly_report save_recommendation_event
save_zoom_call_report search_company_knowledge search_messages search_tasks
search_mail search_zoom_transcripts send_bitrix_message send_mail send_contract
send_owner_recommendations_to_bitrix send_owner_weekly_report_pdf send_telegram_message
send_terms set_agent_team set_agent_tools set_telegram_access
share_drive_item_for_everyone start_here_always_read_ai_instructions update_agent
update_ai_capabilities update_bitrix_task update_crm_deal update_crm_pipeline
update_employee_dossier update_recurring_task upload_file_to_drive
upsert_ai_instruction workspace_add_lead_note workspace_get_conversation
workspace_list_conversations workspace_list_lead_notes workspace_list_urgent
workspace_reply workspace_set_control workspace_set_stage workspace_set_status
write_company_sheet write_google_sheet_values
""".split()
)

# Registry entries that are intentionally removed when their production feature flag is off.
OPTIONAL_REGULAR_TOOL_NAMES = frozenset({"write_company_sheet"})

SELF_TOOL_NAMES = frozenset(
    {
        "delete_my_automation",
        "delete_my_instruction",
        "list_my_automations",
        "list_my_instructions",
        "schedule_my_automation",
        "update_my_automation",
        "upsert_my_instruction",
    }
)

REVIEWED_TOOL_NAMES = REGULAR_TOOL_NAMES | SELF_TOOL_NAMES

ZERO_TOOL_AGENT_SLUGS = frozenset({"albery-ai-bot", "iu-customer-runtime"})

# These actions are too consequential to be inferred from a vague request.  The
# model-visible schema requires ``confirm=true`` and both regular and self-tool
# dispatchers enforce it before a handler, database connection or provider call.
CONFIRMATION_REQUIRED = frozenset(
    {
        "assign_employee_department",
        "create_agent",
        "create_drive_folder",
        "create_google_doc",
        "create_google_sheet",
        "create_telegram_agent",
        "delete_agent",
        "delete_bitrix_task",
        "delete_crm_deal",
        "delete_crm_pipeline",
        "delete_dialog",
        "delete_my_automation",
        "delete_my_instruction",
        "delete_recurring_task",
        "delete_telegram_agent",
        "delete_zoom_call_report",
        "dispatch_leader_evaluations_digest",
        "dispatch_owner_weekly_report_task",
        "dispatch_zoom_operational_tasks",
        "dispatch_zoom_participant_reports",
        "edit_google_doc",
        "join_telegram_chat",
        "manage_apps_script",
        "manage_bitrix_department",
        "manage_crm_deal_field",
        "manage_crm_pipeline_stage",
        "notify_client_when_task_done",
        "notify_iu_group",
        "organize_drive_folder",
        "remove_drive_item_from_folder",
        "reopen_bitrix_task",
        "resolve_dialog_errors",
        "send_bitrix_message",
        "send_contract",
        "send_owner_recommendations_to_bitrix",
        "send_owner_weekly_report_pdf",
        "send_telegram_message",
        "send_terms",
        "set_agent_team",
        "set_agent_tools",
        "set_telegram_access",
        "share_drive_item_for_everyone",
        "update_agent",
        "update_ai_capabilities",
        "update_employee_dossier",
        "update_my_automation",
        "upsert_ai_instruction",
        "write_company_sheet",
    }
)

_READ_PREFIXES = (
    "get_",
    "list_",
    "search_",
    "find_",
    "read_",
    "fetch_",
    "preview_",
    "check_",
    "validate_",
    "calculate_",
    "compare_",
    "analyze_",
    "inspect_",
)
_READ_EXACT = frozenset(
    {
        "health",
        "start_here_always_read_ai_instructions",
        "workspace_get_conversation",
        "workspace_list_conversations",
        "workspace_list_lead_notes",
        "workspace_list_urgent",
    }
)
_LOCAL_ARTIFACT_WRITES = frozenset(
    {
        "convert_document",
        "edit_attachment_document",
        "export_document",
        "export_zoom_call_markdown",
        "export_zoom_transcripts_markdown",
        "process_chat_ocr",
    }
)
_DESTRUCTIVE = frozenset(name for name in REVIEWED_TOOL_NAMES if name.startswith("delete_")) | {
    "remove_drive_item_from_folder",
}
_PRIVILEGED_CONFIGURATION = frozenset(
    {
        "assign_employee_department",
        "create_agent",
        "create_telegram_agent",
        "join_telegram_chat",
        "manage_apps_script",
        "manage_bitrix_department",
        "manage_crm_deal_field",
        "manage_crm_pipeline_stage",
        "resolve_dialog_errors",
        "set_agent_team",
        "set_agent_tools",
        "set_telegram_access",
        "share_drive_item_for_everyone",
        "update_agent",
        "update_ai_capabilities",
        "update_employee_dossier",
        "upsert_ai_instruction",
    }
)


@dataclass(frozen=True)
class ToolPolicy:
    domain: str
    effect: str
    confirmation: str
    sensitive_data: bool
    automation_effect_ledger: bool
    business_object_lock: bool


def _domain(name: str) -> str:
    if name in SELF_TOOL_NAMES:
        return "agent-self-service"
    if name.startswith("workspace_"):
        return "client-funnel"
    if "telegram" in name or "news" in name or name == "get_tg_news":
        return "telegram"
    if "contract" in name or name == "send_terms":
        return "legal-contracts"
    if "dialog" in name or name in {
        "get_chat_ocr_status",
        "get_chat_transcript",
        "list_chats",
        "notify_iu_group",
        "process_chat_ocr",
        "search_messages",
    }:
        return "communications-archive"
    if "zoom" in name:
        return "zoom"
    if any(part in name for part in ("google", "sheet", "drive", "apps_script")):
        return "google-workspace"
    if "crm" in name or "deal" in name:
        return "bitrix-crm"
    if "task" in name or "bitrix" in name:
        return "bitrix-tasks-and-chat"
    if "agent" in name:
        return "agent-management"
    if any(part in name for part in ("owner", "report", "recommendation", "leader", "period")):
        return "management-reporting"
    if any(part in name for part in ("employee", "department", "org_structure", "absence")):
        return "people-and-org"
    if "wb_" in name:
        return "wildberries"
    if any(part in name for part in ("company", "knowledge", "instruction", "document", "attachment", "export")):
        return "knowledge-and-documents"
    if name in {
        "health", "get_runtime_status", "get_context_guide", "get_ai_capabilities",
        "update_ai_capabilities",
    }:
        return "runtime"
    if name in {"fetch_url", "get_webapp_template", "list_available_sources"}:
        return "content-retrieval"
    return "operations"


def _effect(name: str) -> str:
    if name in _READ_EXACT or name.startswith(_READ_PREFIXES):
        return "read"
    if name in _DESTRUCTIVE:
        return "destructive"
    if name in _PRIVILEGED_CONFIGURATION:
        return "privileged-configuration"
    if name in _LOCAL_ARTIFACT_WRITES:
        return "local-artifact-write"
    return "write"


def policy_for(name: str) -> ToolPolicy:
    normalized = str(name or "").strip()
    if normalized not in REVIEWED_TOOL_NAMES:
        raise KeyError(f"unreviewed MCP tool: {normalized or '<empty>'}")
    effect = _effect(normalized)
    domain = _domain(normalized)
    mutating = effect != "read"
    sensitive = domain in {
        "agent-management",
        "bitrix-crm",
        "bitrix-tasks-and-chat",
        "client-funnel",
        "management-reporting",
        "people-and-org",
        "telegram",
        "zoom",
    }
    return ToolPolicy(
        domain=domain,
        effect=effect,
        confirmation="explicit" if normalized in CONFIRMATION_REQUIRED else "none",
        sensitive_data=sensitive,
        automation_effect_ledger=mutating and normalized not in SELF_TOOL_NAMES,
        business_object_lock=mutating and normalized not in SELF_TOOL_NAMES,
    )


def requires_confirmation(name: str) -> bool:
    return str(name or "").strip() in CONFIRMATION_REQUIRED


def apply_confirmation_schemas(specs: Mapping[str, dict[str, Any]]) -> None:
    """Expose the central confirmation contract to the model for applicable tools."""
    for name in CONFIRMATION_REQUIRED & set(specs):
        schema = specs[name].setdefault("inputSchema", {"type": "object"})
        properties = schema.setdefault("properties", {})
        properties.setdefault(
            "confirm",
            {
                "type": "boolean",
                "description": "Явное подтверждение пользователем именно этого действия.",
            },
        )
        required = list(schema.get("required") or [])
        if "confirm" not in required:
            required.append("confirm")
        schema["required"] = required


def validate_registry(names: set[str] | frozenset[str], *, regular: bool) -> None:
    """Reject an unreviewed name; optional production tools may be absent at runtime."""
    reviewed = REGULAR_TOOL_NAMES if regular else SELF_TOOL_NAMES
    unknown = set(names) - set(reviewed)
    if unknown:
        raise RuntimeError(f"unreviewed MCP tools: {', '.join(sorted(unknown))}")
