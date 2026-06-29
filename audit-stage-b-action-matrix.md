# Этап B — матрица UI/API/MCP действий и правил подтверждения

Дата: 2026-06-02
Режим: read-only аудит локальной копии. Прод, секреты и БД не трогались. Код не менялся.

## 1. Главный вывод по активным контурам

- Backend содержит 113 Flask-маршрутов.
- Из них `/api/*`: 75 маршрутов.
- Но общий `before_request` возвращает 410 для любого `/api/*`, если не включён `ALLOW_LEGACY_HTTP_API=1`. Значит в обычном режиме HTTP API — legacy-поверхность, а рабочий управленческий контур должен быть MCP-first.
- Активные без legacy-флага контуры: web UI shell/assets, MCP/SSE endpoints, tokenized Zoom-export download, webhook endpoints Bitrix/Zoom/Google Drive.
- Frontend при этом всё ещё содержит 54 вызова `/api/*`. Это архитектурная несостыковка: UI существует как legacy/admin-интерфейс и зависит от флага, либо часть UI уже не должна считаться рабочей поверхностью.

## 2. Сводка по backend-маршрутам

По режимам:
- active_web_ui_or_download: 17
- active_mcp: 16
- legacy_http_api_only_by_default_410: 75
- active_webhook: 4
- active: 1

По риску:
- read_or_protocol: 21
- db_write_or_workflow: 51
- read_only: 26
- external_action: 5
- preview_read: 1
- local_export_or_download: 5
- webhook_ingest_sync: 4

## 3. Активные контуры без legacy API

### 3.1. MCP / SSE протокол
- GET `/mcp` → `mcp_info`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- GET `/mcp/<path:path_token>` → `mcp_info`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- POST `/mcp` → `mcp_http`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- POST `/mcp/<path:path_token>` → `mcp_http`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- GET `/mcp-faq` → `mcp_faq_info`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- GET `/mcp-faq/<path:path_token>` → `mcp_faq_info`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- POST `/mcp-faq` → `mcp_faq_http`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- POST `/mcp-faq/<path:path_token>` → `mcp_faq_http`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- GET `/sse` → `mcp_sse`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- GET `/sse/<path:path_token>` → `mcp_sse`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- GET `/sse-faq` → `mcp_faq_sse`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- GET `/sse-faq/<path:path_token>` → `mcp_faq_sse`: read_or_protocol; режим: active_mcp; подтверждение: not_required
- POST `/mcp/messages/<session_id>` → `mcp_sse_messages`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- POST `/mcp/messages/<session_id>/<path:path_token>` → `mcp_sse_messages`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- POST `/mcp-faq/messages/<session_id>` → `mcp_faq_sse_messages`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended
- POST `/mcp-faq/messages/<session_id>/<path:path_token>` → `mcp_faq_sse_messages`: db_write_or_workflow; режим: active_mcp; подтверждение: review_recommended

Риск: сами протокольные маршруты активны; конкретные действия определяются MCP-инструментами из `mcp/context_server.py`. Правило — применять MCP-классификацию из `audit-stage-b-mcp-boundary.md`.

### 3.2. Web UI shell / assets / downloads
- GET `/login` → `login`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- POST `/login` → `login_submit`: db_write_or_workflow; режим: active_web_ui_or_download; подтверждение: review_recommended
- GET `/logout` → `logout`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/` → `index`: read_only; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/main` → `index`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/registry` → `index`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/reports` → `index`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/tasks/reports` → `index`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/tasks/registry` → `index`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/assets/<path:filename>` → `frontend_assets`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/favicon.ico` → `frontend_favicon`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/favicon.svg` → `frontend_favicon`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/favicon-16x16.png` → `frontend_favicon`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/favicon-32x32.png` → `frontend_favicon`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/favicon-64x64.png` → `frontend_favicon`: read_or_protocol; режим: active_web_ui_or_download; подтверждение: not_required
- GET `/zoom-export/<int:expires_at>/<token>/<path:filename>` → `zoom_export_download`: local_export_or_download; режим: active_web_ui_or_download; подтверждение: not_required_or_token
- GET `/download/<path:filename>` → `download_export`: local_export_or_download; режим: active_web_ui_or_download; подтверждение: not_required_or_token

### 3.3. Вебхуки интеграций
- GET/POST `/bitrix/events/team/<secret>` → `bitrix_team_event_webhook`: webhook_ingest_sync; режим: active_webhook; подтверждение: secret_signature_not_owner_confirm
- GET/POST `/bitrix/events/tasks/<secret>` → `bitrix_task_event_webhook`: webhook_ingest_sync; режим: active_webhook; подтверждение: secret_signature_not_owner_confirm
- GET/POST `/zoom/events/<secret>` → `zoom_recording_event_webhook`: webhook_ingest_sync; режим: active_webhook; подтверждение: secret_signature_not_owner_confirm
- GET/POST `/google-drive/events/<secret>` → `google_drive_event_webhook`: webhook_ingest_sync; режим: active_webhook; подтверждение: secret_signature_not_owner_confirm

Риск: вебхуки не требуют owner-confirm, потому что это автоматический ingest по секрету/подписи. Для стандарта их надо выделить отдельно: `webhook_ingest_sync`, а не смешивать с ручными действиями.

## 4. Legacy HTTP API: группы риска

Важно: все пункты ниже по умолчанию закрыты ответом 410, пока не включён legacy-флаг. Но код и UI остаются в репозитории, поэтому их надо стандартизировать или явно удалить/задокументировать как legacy.

### 4.1. Чтение / preview / экспорт
- GET `/api/registry` → `api_registry`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats` → `api_chats`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/team` → `api_team`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/zoom-calls` → `api_zoom_calls`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/zoom-calls/<call_id>` → `api_zoom_call_detail`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/zoom-calls/<call_id>/dispatch-operational-tasks/preview` → `api_zoom_call_dispatch_operational_tasks_preview`: preview_read; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/zoom-calls/<call_id>/export.md` → `api_zoom_call_export_markdown`: local_export_or_download; режим: legacy_http_api_only_by_default_410; подтверждение: not_required_or_token
- GET `/api/zoom-export.md` → `api_zoom_calls_export_markdown`: local_export_or_download; режим: legacy_http_api_only_by_default_410; подтверждение: not_required_or_token
- GET `/api/work-items` → `api_work_items`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/company-profile` → `api_company_profile_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/company-folders` → `api_company_folders_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/ai-instruction-folders` → `api_ai_instruction_folders_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/prompts` → `api_prompts`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/ai-requests` → `api_ai_requests_list`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/prompts/history` → `api_prompts_history`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chat-goals` → `api_chat_goals`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/overall-daily-report` → `api_chats_overall_daily_report_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/overall-daily-reports` → `api_chats_overall_daily_reports_list`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/overall-weekly-report` → `api_chats_overall_weekly_report_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/overall-weekly-reports` → `api_chats_overall_weekly_reports_list`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/owner/daily-report` → `api_owner_daily_report_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/owner/daily-reports` → `api_owner_daily_reports_list`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/owner/weekly-report` → `api_owner_weekly_report_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/owner/weekly-reports` → `api_owner_weekly_reports_list`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/<path:dialog_id>/day` → `api_chat_day`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/chats/<path:dialog_id>/weekly-report` → `api_chat_weekly_report_get`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/task/<int:task_id>` → `task_json`: read_only; режим: legacy_http_api_only_by_default_410; подтверждение: not_required
- GET `/api/registry/export` → `api_registry_export`: local_export_or_download; режим: legacy_http_api_only_by_default_410; подтверждение: not_required_or_token

### 4.2. Записи в БД, генерации, синхронизации, OCR
- POST `/api/zoom-calls/<call_id>/report` → `api_zoom_call_report_generate`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/zoom-calls/<call_id>/report` → `api_zoom_call_report_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/sync/full` → `api_full_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/bitrix/task-events/process` → `api_process_bitrix_task_events`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/zoom-events/process` → `api_process_zoom_recording_events`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/zoom-calls/sync` → `api_zoom_calls_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/zoom-calls/sync-google-drive` → `api_zoom_calls_sync_google_drive`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- PUT `/api/company-profile` → `api_company_profile_put`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/company-folders` → `api_company_folders_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- PUT `/api/company-folders/<folder_id>` → `api_company_folders_put`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/company-folders/<folder_id>` → `api_company_folders_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/company-folders/sync-google-drive` → `api_company_folders_sync_google_drive`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/ai-instruction-folders` → `api_ai_instruction_folders_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- PUT `/api/ai-instruction-folders/<folder_id>` → `api_ai_instruction_folders_put`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/ai-instruction-folders/<folder_id>` → `api_ai_instruction_folders_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/prompts` → `api_prompts_save`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/prompts/<prompt_id>` → `api_prompt_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/work-items/sync` → `api_work_items_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/work-items/analyze-chats` → `api_work_items_analyze_chats`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/images/process` → `api_chats_images_process`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/work-items/analyze-attachments` → `api_work_items_analyze_attachments`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/analyze-work` → `api_chat_analyze_work`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- PATCH `/api/reports/<report_type>/<report_id>` → `api_report_manual_edit`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/reports/<report_type>/<report_id>` → `api_report_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chat-goals` → `api_chat_goals_create`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/team/sync` → `api_team_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/sync` → `api_chats_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/exclude` → `api_chat_exclude`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/daily-sync` → `api_chats_daily_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/overall-daily-report` → `api_chats_overall_daily_report_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/overall-weekly-report` → `api_chats_overall_weekly_report_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/owner/daily-report` → `api_owner_daily_report_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/owner/weekly-report` → `api_owner_weekly_report_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/pipeline/run-day` → `api_pipeline_run_day`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/pipeline/run-week` → `api_pipeline_run_week`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/messages/sync` → `api_chat_messages_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/images/process` → `api_chat_images_process`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/report` → `api_chat_report`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/chats/<path:dialog_id>/report` → `api_chat_report_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/chats/<path:dialog_id>/weekly-report` → `api_chat_weekly_report_post`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- DELETE `/api/chats/<path:dialog_id>/weekly-report` → `api_chat_weekly_report_delete`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended
- POST `/api/registry/sync` → `api_registry_sync`: db_write_or_workflow; режим: legacy_http_api_only_by_default_410; подтверждение: review_recommended

### 4.3. Внешние действия и отправки
- POST `/api/zoom-calls/<call_id>/dispatch-operational-tasks` → `api_zoom_call_dispatch_operational_tasks`: external_action; режим: legacy_http_api_only_by_default_410; подтверждение: missing_or_ui_only
- POST `/api/owner/daily-reports/<report_id>/send` → `api_owner_daily_report_send`: external_action; режим: legacy_http_api_only_by_default_410; подтверждение: missing_or_ui_only
- POST `/api/owner/daily-reports/<report_id>/send-full` → `api_owner_daily_report_send_full`: external_action; режим: legacy_http_api_only_by_default_410; подтверждение: missing_or_ui_only
- POST `/api/owner/weekly-reports/<report_id>/send` → `api_owner_weekly_report_send`: external_action; режим: legacy_http_api_only_by_default_410; подтверждение: missing_or_ui_only
- POST `/api/owner/weekly-reports/<report_id>/send-full` → `api_owner_weekly_report_send_full`: external_action; режим: legacy_http_api_only_by_default_410; подтверждение: missing_or_ui_only

Наблюдение: в HTTP legacy API внешние отправки выполняются POST-маршрутами без явного `confirm=true` на уровне маршрута. Возможно, подтверждение есть только в UI через модалки/кнопки. Для стандарта этого недостаточно: внешнее действие должно иметь server-side confirm-gate, как уже сделано в части MCP-инструментов.

## 5. Frontend-вызовы `/api/*`

Найдено 54 вызова из `Интерфейс/src/App.tsx`. Они относятся к legacy API и при обычном MCP-first режиме получат 410, если `ALLOW_LEGACY_HTTP_API` не включён.

- строка 2216: GET ``/api/registry?${params.toString(` — read
- строка 2243: GET `"/api/owner/daily-reports?limit=100"` — read
- строка 2262: GET `"/api/owner/weekly-reports?limit=100"` — read
- строка 2295: GET `"/api/ai-requests?limit=150"` — read
- строка 2327: POST `"/api/registry/sync"` — write/action candidate
- строка 2355: POST `"/api/sync/full"` — write/action candidate
- строка 2384: GET `"/api/team"` — read
- строка 2583: GET ``/api/chat-goals?${params.toString(` — read
- строка 2618: POST `"/api/chat-goals"` — write/action candidate
- строка 2647: GET ``/api/company-folders?${params.toString()}`` — read
- строка 2665: POST `"/api/company-folders/sync-google-drive"` — write/action candidate
- строка 2694: POST `"/api/company-folders"` — write/action candidate
- строка 2732: PUT ``/api/company-folders/${encodeURIComponent(folder.id` — write/action candidate
- строка 2761: DELETE ``/api/company-folders/${encodeURIComponent(folder.id` — write/action candidate
- строка 2784: PUT ``/api/company-folders/${encodeURIComponent(companyCurrentFolder.id` — write/action candidate
- строка 2807: GET ``/api/ai-instruction-folders?${params.toString()}`` — read
- строка 2832: POST `"/api/ai-instruction-folders"` — write/action candidate
- строка 2870: PUT ``/api/ai-instruction-folders/${encodeURIComponent(folder.id` — write/action candidate
- строка 2893: PUT ``/api/ai-instruction-folders/${encodeURIComponent(aiInstructionCurrentFolder.id` — write/action candidate
- строка 2921: DELETE ``/api/ai-instruction-folders/${encodeURIComponent(folder.id` — write/action candidate
- строка 3550: POST `"/api/team/sync"` — write/action candidate
- строка 3566: GET ``/api/prompts/history?prompt_key=${encodeURIComponent(key` — read
- строка 3579: GET `"/api/prompts"` — read
- строка 3612: POST `"/api/prompts"` — write/action candidate
- строка 3643: DELETE ``/api/prompts/${encodeURIComponent(version.id` — write/action candidate
- строка 3661: GET ``/api/registry/export?${params.toString(` — read
- строка 3677: GET `"/api/chats"` — read
- строка 3754: POST `"/api/chats/sync"` — write/action candidate
- строка 3770: POST `"/api/chats/daily-sync"` — write/action candidate
- строка 3794: POST `"/api/chats/daily-sync"` — write/action candidate
- строка 3813: GET ``/api/chats/overall-daily-report?date=${encodeURIComponent(dateValue` — read
- строка 3836: POST `"/api/zoom-calls"` — write/action candidate
- строка 3854: POST `"/api/zoom-calls/sync?from=2026-01-01"` — write/action candidate
- строка 3880: POST `"/api/zoom-calls/sync-google-drive"` — write/action candidate
- строка 3904: POST ``/api/zoom-calls/${encodeURIComponent(call.id)}`` — write/action candidate
- строка 3917: POST ``/api/zoom-calls/${encodeURIComponent(call.id)}/report`` — write/action candidate
- строка 3947: DELETE ``/api/zoom-calls/${encodeURIComponent(call.id)}/report`` — write/action candidate
- строка 4273: GET `"/api/chats/overall-weekly-reports?limit=30"` — read
- строка 4295: GET `"/api/chats/overall-daily-reports?limit=30"` — read
- строка 4323: POST `"/api/chats/overall-weekly-report"` — write/action candidate
- строка 4354: POST `"/api/chats/overall-daily-report"` — write/action candidate
- строка 4378: POST `"/api/chats/images/process"` — write/action candidate
- строка 4401: POST ``/api/chats/${encodeURIComponent(chat.dialog_id` — write/action candidate
- строка 4523: POST ``/api/chats/${encodeURIComponent(chat.dialog_id` — write/action candidate
- строка 4528: POST ``/api/chats/${encodeURIComponent(chat.dialog_id` — write/action candidate
- строка 4543: GET ``/api/chats/${encodeURIComponent(chat.dialog_id` — read
- строка 4626: GET ``/api/chats/${encodeURIComponent(dialogId` — read
- строка 4642: POST ``/api/chats/${encodeURIComponent(selectedChat.dialog_id` — write/action candidate
- строка 4679: POST ``/api/chats/${encodeURIComponent(selectedChat.dialog_id` — write/action candidate
- строка 4708: POST `"/api/chats/daily-sync"` — write/action candidate
- строка 4794: POST ``/api/chats/${encodeURIComponent(selectedChat.dialog_id` — write/action candidate
- строка 4961: GET ``/api/owner/daily-report?date=${encodeURIComponent(prevIso)}`);` — read
- строка 4978: GET ``/api/chats/overall-weekly-report?period_start=${encodeURIComponent(prevStartIso)}&period_end=${encodeURIComponent(prevEndIso)}`)` — read
- строка 4979: GET ``/api/owner/weekly-report?period_start=${encodeURIComponent(prevStartIso)}&period_end=${encodeURIComponent(prevEndIso)}`)` — read

## 6. Единый стандарт, который надо ввести

Классы действий:
- `read_only` — только чтение локальной БД/контекста.
- `external_read` — внешний GET/fetch без записи.
- `local_export` — создание локального файла/ссылки.
- `webhook_ingest_sync` — автоматический входящий webhook по секрету/подписи.
- `db_write_draft` — запись черновика/события/версии без внешней отправки.
- `db_write_current` — изменение текущей версии, статуса или живых AI-инструкций.
- `external_action` — создание/удаление задач, отправка сообщений, PDF, рекомендаций во внешние системы.

Правила подтверждения:
- `read_only`: без подтверждения.
- `external_read`: allowlist или явное разрешение на домен/ссылку.
- `local_export`: без confirm, но с сообщением, что создан файл/ссылка и срок её жизни.
- `webhook_ingest_sync`: не owner-confirm, а проверка секрета/подписи, идемпотентность и логирование.
- `db_write_draft`: допустимо без confirm, если не меняет текущую версию и поведение системы.
- `db_write_current`: preview/summary изменения перед записью; для AI-инструкций — особенно строго.
- `external_action`: всегда server-side `confirm=true` + preview точного получателя/текста/задачи + явное одобрение владельца.

## 7. Приоритетные несостыковки для исправления

1. `create_bitrix_task` в MCP — добавить обязательный confirm-gate и/или отдельный preview-инструмент.
2. Legacy HTTP отправки в Bitrix (`/api/owner/.../send`, `/send-full`, `/api/zoom-calls/.../dispatch-operational-tasks`) — если legacy API будет использоваться, добавить server-side подтверждение; если не будет — явно задокументировать как отключённое.
3. `process_chat_ocr` — переклассифицировать из “служебного чтения” в workflow-запись; решить, нужен ли confirm или достаточно ограничений по дате/чату/force.
4. `upsert_ai_instruction` и UI-редактирование AI-инструкций — считать изменением поведения агента; добавить версионность/preview/журнал принятия изменений.
5. `fetch_url` — добавить allowlist/denylist и запрет внутренних адресов, чтобы не превращать MCP в произвольный сетевой fetcher.
6. Документацию обновить: `mcp/README.md` не должен говорить read-only без оговорок; root `README.md` должен описывать реальный Albery, а не старый Bitrix weekly export.

## 8. Что делать дальше

- Если цель этапа B — только аудит: этап B можно считать закрытым после ревью этой матрицы.
- Если цель — сразу стандартизировать: следующий безопасный кодовый шаг — маленький патч confirm-gate для `create_bitrix_task` + обновление `mcp/README.md`. Но это уже изменение кода/документации, не read-only аудит.
- Для этапа C: перейти к качеству/рискам: тесты, CI, god-objects, legacy API/UI drift, документационный каркас проекта.
