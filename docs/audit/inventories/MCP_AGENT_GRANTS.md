# Production MCP agent grants

Read-only snapshot from server 186 on 2026-08-13. `yes` means the tool was actually
returned by the private profile connector after DB switches and the manifest cap were applied.

| Profile | Mode | Effective tools |
| --- | --- | ---: |
| `agent-finansist` | `custom` | 110 |
| `agent-po-rabote-s-iu` | `custom` | 137 |
| `agent-razrabotchik` | `max` | 166 |
| `agent-sklad` | `custom` | 109 |
| `albery-ai-bot` | `custom` | 0 |
| `iu-customer-runtime` | `custom` | 0 |
| `main` | `custom` | 116 |
| `menedzher-marketpleysa` | `custom` | 141 |
| `novostnoy-agent` | `custom` | 20 |

## Exact grant matrix

| Tool | `agent-finansist` | `agent-po-rabote-s-iu` | `agent-razrabotchik` | `agent-sklad` | `albery-ai-bot` | `iu-customer-runtime` | `main` | `menedzher-marketpleysa` | `novostnoy-agent` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `add_bitrix_task_comment` | yes | yes | yes | yes |  |  | yes | yes |  |
| `add_deal_comment` |  |  | yes |  |  |  |  | yes |  |
| `add_task_checklist` | yes | yes | yes | yes |  |  | yes | yes |  |
| `add_task_reminder` | yes | yes | yes | yes |  |  | yes | yes |  |
| `assign_employee_department` |  | yes | yes |  |  |  | yes | yes |  |
| `attach_files_to_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `cancel_owner_recommendation` | yes | yes | yes | yes |  |  | yes | yes |  |
| `complete_bitrix_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `convert_document` |  |  | yes |  |  |  |  | yes |  |
| `create_agent` |  |  | yes |  |  |  |  |  |  |
| `create_bitrix_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_crm_deal` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_crm_pipeline` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_drive_folder` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_google_doc` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_google_sheet` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_recurring_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `create_telegram_agent` |  | yes | yes |  |  |  |  | yes |  |
| `delete_agent` |  |  | yes |  |  |  |  |  |  |
| `delete_bitrix_task` |  |  | yes |  |  |  |  |  |  |
| `delete_crm_deal` |  |  | yes |  |  |  |  |  |  |
| `delete_crm_pipeline` |  |  | yes |  |  |  |  |  |  |
| `delete_dialog` |  |  | yes |  |  |  |  |  |  |
| `delete_my_automation` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `delete_my_instruction` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `delete_recurring_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `delete_telegram_agent` |  | yes | yes |  |  |  |  | yes |  |
| `delete_zoom_call_report` |  |  | yes |  |  |  |  |  |  |
| `dispatch_leader_evaluations_digest` | yes | yes | yes | yes |  |  | yes | yes |  |
| `dispatch_owner_weekly_report_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `dispatch_zoom_operational_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `dispatch_zoom_participant_reports` | yes | yes | yes | yes |  |  | yes | yes |  |
| `edit_attachment_document` | yes | yes | yes | yes |  |  | yes | yes |  |
| `edit_google_doc` | yes | yes | yes | yes |  |  | yes | yes |  |
| `export_document` | yes | yes | yes | yes |  |  | yes | yes |  |
| `export_zoom_call_markdown` | yes | yes | yes | yes |  |  | yes | yes |  |
| `export_zoom_transcripts_markdown` | yes | yes | yes | yes |  |  | yes | yes |  |
| `fetch_url` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `format_google_sheet` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_agent_decisions` |  |  | yes |  |  |  |  | yes |  |
| `get_agent_link` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `get_agent_monitoring` |  |  | yes |  |  |  |  |  |  |
| `get_ai_capabilities` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_ai_instructions` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `get_attachment_text` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_bitrix_bot_chat` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_bitrix_departments` |  | yes | yes |  |  |  | yes | yes |  |
| `get_chat_ocr_status` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_chat_transcript` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_compact_export` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_company_file` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `get_company_profile` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_context_guide` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `get_crm_deal` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_employee_absences` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_employee_dossier` |  |  | yes |  |  |  |  |  |  |
| `get_google_sheet_meta` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_latest_news_digest` |  | yes | yes |  |  |  | yes | yes | yes |
| `get_org_structure` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_owner_reports` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_period_index` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_previous_owner_daily_context` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_recommendation_feedback_context` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_report_contract` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_report_readiness` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_runtime_status` |  | yes | yes |  |  |  | yes | yes |  |
| `get_task_comments` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_task_history` |  |  | yes |  |  |  |  | yes |  |
| `get_telegram_dialog` |  | yes | yes |  |  |  |  |  |  |
| `get_tg_news` |  | yes | yes |  |  |  |  | yes | yes |
| `get_wb_prices` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_webapp_template` | yes | yes | yes | yes |  |  | yes | yes |  |
| `get_zoom_call_transcript` | yes | yes | yes | yes |  |  | yes | yes |  |
| `health` |  | yes | yes |  |  |  | yes | yes |  |
| `join_telegram_chat` |  |  | yes |  |  |  |  |  | yes |
| `link_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_agents` |  |  | yes |  |  |  |  |  |  |
| `list_available_sources` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_bitrix_bot_sessions` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_chats` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_company_files` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `list_crm_deal_fields` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_crm_deals` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_crm_forms` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_crm_lead_contacts` |  | yes | yes |  |  |  |  | yes |  |
| `list_crm_pipelines` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_dialog_errors` |  |  | yes |  |  |  |  |  |  |
| `list_drive_folder_items` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_drive_folders` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_leader_evaluations` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_my_automations` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `list_my_instructions` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `list_overdue_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_pending_owner_recommendations` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_pending_zoom_operational_dispatches` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_periods` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_recommendations` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_recurring_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_task_userfields` | yes | yes | yes | yes |  |  | yes | yes |  |
| `list_telegram_agents` |  | yes | yes |  |  |  |  | yes |  |
| `list_telegram_chats` |  |  | yes |  |  |  |  |  | yes |
| `list_telegram_contacts` |  | yes | yes |  |  |  |  |  |  |
| `list_zoom_calls` | yes | yes | yes | yes |  |  | yes | yes |  |
| `log_task_time` | yes | yes | yes | yes |  |  | yes | yes |  |
| `make_sheet_applet` | yes | yes | yes | yes |  |  | yes | yes |  |
| `manage_apps_script` | yes | yes | yes | yes |  |  | yes | yes |  |
| `manage_bitrix_department` |  | yes | yes |  |  |  | yes | yes |  |
| `manage_crm_deal_field` | yes | yes | yes | yes |  |  | yes | yes |  |
| `manage_crm_pipeline_stage` | yes | yes | yes | yes |  |  | yes | yes |  |
| `move_drive_file_to_folder` | yes | yes | yes | yes |  |  | yes | yes |  |
| `next_contract_number` |  | yes | yes |  |  |  |  | yes |  |
| `notify_client_when_task_done` |  | yes | yes |  |  |  |  | yes |  |
| `notify_iu_group` |  | yes | yes |  |  |  |  | yes |  |
| `organize_drive_folder` | yes | yes | yes | yes |  |  | yes | yes |  |
| `preview_zoom_operational_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `preview_zoom_participant_reports` | yes | yes | yes | yes |  |  | yes | yes |  |
| `process_chat_ocr` | yes | yes | yes | yes |  |  | yes | yes |  |
| `read_google_doc` | yes | yes | yes | yes |  |  | yes | yes |  |
| `read_google_sheet_values` | yes | yes | yes | yes |  |  | yes | yes |  |
| `read_telegram_chat` |  |  | yes |  |  |  |  |  | yes |
| `remove_drive_item_from_folder` | yes | yes | yes | yes |  |  | yes | yes |  |
| `reopen_bitrix_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `report_overdue_discipline` |  |  | yes |  |  |  |  | yes |  |
| `resolve_dialog_errors` |  |  | yes |  |  |  |  |  |  |
| `save_news_digest` |  | yes | yes |  |  |  |  | yes | yes |
| `save_owner_daily_report` | yes | yes | yes | yes |  |  | yes | yes |  |
| `save_owner_weekly_report` | yes | yes | yes | yes |  |  | yes | yes |  |
| `save_recommendation_event` | yes | yes | yes | yes |  |  | yes | yes |  |
| `save_zoom_call_report` | yes | yes | yes | yes |  |  | yes | yes |  |
| `schedule_my_automation` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `search_company_knowledge` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `search_messages` | yes | yes | yes | yes |  |  | yes | yes |  |
| `search_tasks` | yes | yes | yes | yes |  |  | yes | yes |  |
| `search_zoom_transcripts` | yes | yes | yes | yes |  |  | yes | yes |  |
| `send_bitrix_message` | yes | yes | yes | yes |  |  | yes | yes |  |
| `send_contract` |  | yes | yes |  |  |  |  |  |  |
| `send_owner_recommendations_to_bitrix` | yes | yes | yes | yes |  |  | yes | yes |  |
| `send_owner_weekly_report_pdf` | yes | yes | yes | yes |  |  | yes | yes |  |
| `send_telegram_message` |  | yes | yes |  |  |  |  |  |  |
| `send_terms` |  | yes | yes |  |  |  |  | yes |  |
| `set_agent_team` |  |  | yes |  |  |  |  |  |  |
| `set_agent_tools` |  |  | yes |  |  |  |  |  |  |
| `set_telegram_access` |  | yes | yes |  |  |  |  | yes |  |
| `share_drive_item_for_everyone` | yes | yes | yes | yes |  |  | yes | yes |  |
| `start_here_always_read_ai_instructions` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `update_agent` |  |  | yes |  |  |  |  |  |  |
| `update_ai_capabilities` |  |  | yes |  |  |  |  |  |  |
| `update_bitrix_task` | yes | yes | yes | yes |  |  | yes | yes |  |
| `update_crm_deal` | yes | yes | yes | yes |  |  | yes | yes |  |
| `update_crm_pipeline` | yes | yes | yes | yes |  |  | yes | yes |  |
| `update_employee_dossier` |  |  | yes |  |  |  |  |  |  |
| `update_recurring_task` | yes | yes | yes |  |  |  | yes | yes |  |
| `upload_file_to_drive` | yes | yes | yes | yes |  |  | yes | yes |  |
| `upsert_ai_instruction` |  |  | yes |  |  |  |  |  |  |
| `upsert_my_instruction` | yes | yes | yes | yes |  |  | yes | yes | yes |
| `workspace_add_lead_note` |  |  | yes |  |  |  |  | yes |  |
| `workspace_get_conversation` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_list_conversations` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_list_lead_notes` |  |  | yes |  |  |  |  | yes |  |
| `workspace_list_urgent` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_reply` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_set_control` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_set_stage` |  | yes | yes |  |  |  |  | yes |  |
| `workspace_set_status` |  | yes | yes |  |  |  |  | yes |  |
| `write_company_sheet` | yes |  | yes | yes |  |  | yes | yes |  |
| `write_google_sheet_values` | yes | yes | yes | yes |  |  | yes | yes |  |
