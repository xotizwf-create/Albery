# MCP capability inventory

Generated from the versioned runtime registry and `mcp.tool_policy`.
The inventory includes 160 regular and 6 profile self-service tools.

| Tool | Domain | Effect | Confirm | Sensitive | Automation ledger / lock | Human description |
| --- | --- | --- | --- | --- | --- | --- |
| `add_bitrix_task_comment` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Add one comment to an existing Bitrix task discussion |
| `add_deal_comment` | bitrix-crm | write | none | yes | yes / yes | Добавить запись в ленту (таймлайн) сделки CRM |
| `add_task_checklist` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Добавить пункты ЧЕК-ЛИСТА к задаче Bitrix |
| `add_task_reminder` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Добавить НАПОМИНАНИЕ по задаче на конкретное время |
| `assign_employee_department` | people-and-org | privileged-configuration | explicit | yes | yes / yes | Перевести сотрудника(ов) в отдел и/или задать должность |
| `attach_files_to_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Attach one or more files the user sent the bot (screenshots, documents — referenced by their attachment tokens att_…) to a Bitrix task |
| `cancel_owner_recommendation` | management-reporting | write | none | yes | yes / yes | Mark one owner_manager_recommendations row as cancelled (e.g |
| `check_google_sheet_health` | google-workspace | read | none | no | no / no | Check a spreadsheet for LOGICAL defects and get them back as a list |
| `complete_bitrix_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Complete (close/«завершить») one Bitrix task |
| `convert_document` | knowledge-and-documents | local-artifact-write | none | no | yes / yes | Преобразовать присланный документ: PDF → редактируемый Word (target='docx') или Word → PDF (target='pdf') |
| `create_agent` | agent-management | privileged-configuration | explicit | yes | yes / yes | Создать нового субагента (Bitrix-бот зарегистрируется автоматически) |
| `create_bitrix_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Create one Bitrix task through the configured Bitrix webhook |
| `create_crm_deal` | bitrix-crm | write | none | yes | yes / yes | Создать СДЕЛКУ CRM |
| `create_crm_pipeline` | bitrix-crm | write | none | yes | yes / yes | Создать НОВУЮ ВОРОНКУ CRM (направление сделок) |
| `create_drive_folder` | google-workspace | write | explicit | no | yes / yes | Create a Google Drive subfolder inside a specified parent folder, or reuse an existing exact-name subfolder |
| `create_google_doc` | google-workspace | write | explicit | no | yes / yes | Создать НОВЫЙ Google-ДОКУМЕНТ (Google Docs) из HTML от имени Google-аккаунта агента |
| `create_google_sheet` | google-workspace | write | explicit | no | yes / yes | Создать НОВУЮ Google-таблицу (от имени Google-аккаунта агента) |
| `create_recurring_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Создать ПОВТОРЯЮЩУЮСЯ (регулярную) задачу — она будет создаваться автоматически по расписанию (например, каждую пятницу в 10:00 с дедлайном 19:00 того же дня) |
| `create_telegram_agent` | telegram | privileged-configuration | explicit | yes | yes / yes | Завести нового Telegram-агента |
| `delete_agent` | agent-management | destructive | explicit | yes | yes / yes | Удалить субагента (разрегистрирует Bitrix-бота, чистит коннектор и данные) |
| `delete_bitrix_task` | bitrix-tasks-and-chat | destructive | explicit | yes | yes / yes | Delete one Bitrix task through the configured Bitrix webhook |
| `delete_crm_deal` | bitrix-crm | destructive | explicit | yes | yes / yes | УДАЛИТЬ сделку CRM |
| `delete_crm_pipeline` | bitrix-crm | destructive | explicit | yes | yes / yes | УДАЛИТЬ воронку CRM |
| `delete_dialog` | communications-archive | destructive | explicit | no | yes / yes | УДАЛИТЬ ПЕРЕПИСКУ из журнала НАВСЕГДА — по dialog_id или @username |
| `delete_my_automation` | agent-self-service | destructive | explicit | no | no / no | Удалить собственную автоматизацию агента. |
| `delete_my_instruction` | agent-self-service | destructive | explicit | no | no / no | Удалить собственную самообученную инструкцию агента. |
| `delete_recurring_task` | bitrix-tasks-and-chat | destructive | explicit | yes | yes / yes | Остановить ПОВТОРЯЮЩУЮСЯ задачу — планировщик перестанет её создавать |
| `delete_telegram_agent` | telegram | destructive | explicit | yes | yes / yes | Удалить Telegram-агента: опрос останавливается, коннектор и права доступа стираются, журнал переписок остаётся историей |
| `delete_zoom_call_report` | zoom | destructive | explicit | yes | yes / yes | Delete only the AI report for one Zoom call from zoom_calls.analytical_note and raw_json.ai_report |
| `dispatch_leader_evaluations_digest` | management-reporting | write | explicit | yes | yes / yes | Create the weekly leader-evaluation digest as ONE Bitrix task for the owner Евгений Палей: title 'Ознакомиться с оценкой руководителей за период <даты>', description = the approved digest_text, deadline = next calendar d |
| `dispatch_owner_weekly_report_task` | bitrix-tasks-and-chat | write | explicit | yes | yes / yes | Create the weekly owner report as a Bitrix TASK for Евгений Палей with the report PDF attached (UF_TASK_WEBDAV_FILES) |
| `dispatch_zoom_operational_tasks` | zoom | write | explicit | yes | yes / yes | Create aggregated 'Итоги созвона <ЧЧ:ММ>' Bitrix tasks for ONE Zoom call: one task per responsible person, deadline = 18:00 МСК того же дня (если до 18:00 меньше 3 часов или выходной — следующий рабочий день 11:00), desc |
| `dispatch_zoom_participant_reports` | zoom | write | explicit | yes | yes / yes | Create personal participant report Bitrix tasks for one Zoom call: one task per matched participant, containing shared summary and supportive personal feedback |
| `edit_attachment_document` | knowledge-and-documents | local-artifact-write | none | no | yes / yes | Внести ТОЧЕЧНЫЕ правки в присланный пользователем файл (pdf/xlsx/docx/txt/csv/md) по его attachment_id: заменяет только указанные фрагменты текста, всё остальное — структура, таблицы, ссылки, оформление — сохраняется из  |
| `edit_google_doc` | google-workspace | write | explicit | no | yes / yes | Отредактировать СУЩЕСТВУЮЩИЙ Google-документ: заменяет его содержимое переданным HTML, сохраняя тот же документ, ту же ссылку и тот же доступ |
| `export_document` | knowledge-and-documents | local-artifact-write | none | no | yes / yes | Собери НАСТОЯЩИЙ файл документа Word (.docx) из СВОЕГО HTML — ты сам полностью управляешь оформлением (это главный инструмент для договоров, актов, официальных документов) |
| `export_zoom_call_markdown` | zoom | local-artifact-write | none | yes | yes / yes | Export ONE Zoom call as a Markdown document: header with topic, date, time (МСК), duration and participants, then the FULL transcript line by line (speaker + timecode) |
| `export_zoom_transcripts_markdown` | zoom | local-artifact-write | none | yes | yes / yes | Export MANY Zoom calls into ONE Markdown document with a table of contents and clear '---' boundaries between meetings; each meeting has metadata (topic, date, МСК time, duration, participants) plus its FULL transcript |
| `fetch_url` | content-retrieval | read | none | no | no / no | Fetch the contents of a web URL the user sent in chat and return it as readable text |
| `format_google_sheet` | google-workspace | write | none | no | yes / yes | Style a sheet and build dashboards: apply a list of Sheets API batchUpdate request objects |
| `get_agent_decisions` | agent-management | read | none | yes | no / no | ТРАССА РЕШЕНИЙ агента воронки: что он решил, по какому правилу реестра, на каких фактах и что из этого вышло |
| `get_agent_link` | agent-management | read | none | yes | no / no | Выдать пользователю ссылку на чат с профильным агентом компании (Агент Албери, Агент-юрист, Агент-финансист, Агент-разработчик, Новостной агент) И проверить, есть ли у ЭТОГО пользователя доступ к нему |
| `get_agent_monitoring` | agent-management | read | none | yes | no / no | Мониторинг и учёт использования самого агента (живые данные страниц «Центра Агента»): здоровье всех систем (БД, MCP, мозг, Bitrix REST, Zoom, Google Drive, память сервера) с полем problems (что не ок — подсвети владельцу |
| `get_ai_capabilities` | runtime | read | none | no | no / no | Return what YOU (this AI assistant) can do for the current connector/tool set |
| `get_ai_instructions` | knowledge-and-documents | read | none | no | no / no | Read live editable AI behavior and answer-format instructions from Настройки -> Инструкции для ИИ |
| `get_attachment_text` | knowledge-and-documents | read | none | no | no / no | Read the FULL text of a file the user sent the bot (contract, document, screenshot OCR), by its attachment token att_… The prompt shows a preview of long documents; call this to read the WHOLE thing — nothing is truncate |
| `get_bitrix_bot_chat` | bitrix-tasks-and-chat | read | none | yes | no / no | Read the full question→answer transcript of one person's conversation with the AI assistant in Bitrix24 (by dialog_id or bitrix_user_id), to analyze and improve answer quality |
| `get_bitrix_departments` | bitrix-tasks-and-chat | read | none | yes | no / no | ЖИВАЯ оргструктура портала: отделы (id, название, родитель, руководитель) и кто в каждом отделе (с id и должностями) |
| `get_chat_ocr_status` | communications-archive | read | none | no | no / no | Check whether image/PDF attachments for one chat/date already have OCR text before generating a daily report. |
| `get_chat_transcript` | communications-archive | read | none | no | no / no | Get raw chat transcript messages by dialog_id and period, including OCR transcripts for attached images and PDFs. |
| `get_compact_export` | knowledge-and-documents | read | none | no | no / no | Generate a compact export bundle for a period from live PostgreSQL data. |
| `get_company_file` | knowledge-and-documents | read | none | no | no / no | Read the full text and source metadata for one company knowledge file by folder_id or google_file_id. |
| `get_company_profile` | knowledge-and-documents | read | none | no | no / no | Read the editable company profile text from PostgreSQL for business context. |
| `get_context_guide` | runtime | read | none | no | no / no | Read navigation rules after start_here_always_read_ai_instructions: where to search first, which tools map to which business sources, and how to avoid chaotic database exploration |
| `get_crm_deal` | bitrix-crm | read | none | yes | no / no | Одна СДЕЛКА CRM целиком: все заполненные поля, воронка/стадия по-человечески, пользовательские поля, ссылка на портал. |
| `get_employee_absences` | people-and-org | read | none | yes | no / no | Узнать, в отпуске ли / отсутствует ли сотрудник (Bitrix «График отсутствий», портал b24-0xrp3s) |
| `get_employee_dossier` | people-and-org | read | none | yes | no / no | ДОСЬЕ по сотрудникам (внутреннее, для владельца/админа — рядовым не показывать): кто реально работает с агентом (ходы за 30 дней, из них в задачах), реакция на предложения помощи (offers_made/engaged/declined), какие зад |
| `get_google_sheet_meta` | google-workspace | read | none | no | no / no | Read a Google Sheet's structure: its tabs (sheetId / title / grid size) |
| `get_latest_news_digest` | telegram | read | none | yes | no / no | Последняя новостная сводка Новостного агента (сохранённый текст) + её возраст |
| `get_org_structure` | people-and-org | read | none | yes | no / no | Return departments and users with managers and department memberships. |
| `get_owner_reports` | management-reporting | read | none | yes | no / no | Read recent current owner daily or weekly reports |
| `get_period_index` | management-reporting | read | none | yes | no / no | Return counts and top chats for a date period. |
| `get_previous_owner_daily_context` | management-reporting | read | none | yes | no / no | Read only the previous calendar day's current owner daily report as continuity context for creating or checking a new owner daily report. |
| `get_recommendation_feedback_context` | management-reporting | read | none | yes | no / no | Read active recommendations and event history relevant to one chat/date |
| `get_report_contract` | legal-contracts | read | none | no | no / no | Read the active report-generation contract for a configured report category |
| `get_report_readiness` | management-reporting | read | none | yes | no / no | Report-building readiness for a date range in one call: per day, which active chats have messages and which already have a current daily report (missing_daily_reports), which Zoom calls already have an analytical_note (m |
| `get_runtime_status` | runtime | read | none | no | no / no | Inspect MCP-first/PostgreSQL-only runtime mode, database target, cache TTL, and whether legacy HTTP API compatibility is enabled. |
| `get_task_comments` | bitrix-tasks-and-chat | read | none | yes | no / no | Read the discussion/comments of one Bitrix task by bitrix_task_id (from search_tasks) |
| `get_task_history` | bitrix-tasks-and-chat | read | none | yes | no / no | История изменений задачи Битрикса: КТО, какое поле, из чего в что и когда |
| `get_telegram_dialog` | telegram | read | none | yes | no / no | ПЕРЕПИСКА С КЛИЕНТОМ в Telegram по telegram_id или username |
| `get_tg_news` | telegram | read | none | yes | no / no | СВЕЖИЕ ПОСТЫ отраслевых Telegram-каналов (список ведёт владелец: WB/маркетплейсы, оргпрактики, ИИ) |
| `get_wb_prices` | wildberries | read | none | no | no / no | Текущие витринные цены Wildberries для СПИСКА артикулов/ссылок (до 15 за вызов) — используй для заполнения таблиц цен и любых списков, НЕ дергай fetch_url по одному артикулу |
| `get_webapp_template` | content-retrieval | read | none | no | no / no | Get the Albery-branded HTML/CSS web-app template (matches the prod React site: light bg, white rounded cards, primary purple #5440F6, Inter font, soft shadows, styled inputs/buttons/tables/badges) |
| `get_zoom_call_transcript` | zoom | read | none | yes | no / no | Get one Zoom call with factual Zoom participants and raw transcript segments |
| `health` | runtime | read | none | no | no / no | Check PostgreSQL connectivity and MCP server status. |
| `join_telegram_chat` | telegram | privileged-configuration | explicit | yes | yes / yes | Вступить аккаунтом @AlberyAIManager в чат по ссылке-приглашению (t.me/+…) или в публичный канал по @имени |
| `link_tasks` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Связать две задачи (СВЯЗАННЫЕ ЗАДАЧИ / зависимость для Ганта) |
| `list_agents` | agent-management | read | none | yes | no / no | Список всех агентов системы (универсальный + субагенты) с их настройками: имя, должность, вкл/выкл, сколько инструментов включено, команда |
| `list_available_sources` | content-retrieval | read | none | no | no / no | Show which known context tables exist and how many rows each has. |
| `list_bitrix_bot_sessions` | bitrix-tasks-and-chat | read | none | yes | no / no | List conversations/sessions employees had with the AI assistant (Гермес-ассистент) INSIDE Bitrix24 chat — one row per dialog/user with message count, first/last activity, access tier, error count, current session epoch |
| `list_chats` | communications-archive | read | none | no | no / no | List active non-excluded chats, optionally with message counts for a period. |
| `list_company_files` | knowledge-and-documents | read | none | no | no / no | List all files/folders available in the company knowledge section, including Google Drive mirrored documents |
| `list_crm_deal_fields` | bitrix-crm | read | none | yes | no / no | ПОЛЯ СДЕЛОК CRM: пользовательские поля (UF_CRM_*) с кодами/типами/подписями — реальные коды для custom_fields в create_crm_deal/update_crm_deal |
| `list_crm_deals` | bitrix-crm | read | none | yes | no / no | СДЕЛКИ CRM: список с фильтрами по воронке (category_id/pipeline_name), стадии (stage — код или название), ответственному (assigned_name/assigned_bitrix_user_id), тексту в названии (search) |
| `list_crm_forms` | bitrix-crm | read | none | yes | no / no | ФОРМЫ CRM (Битрикс24.Формы): список нативных веб-форм с ПУБЛИЧНОЙ ссылкой (public_url) — её шлют кандидатам, заявки создают сделки в привязанной к форме воронке |
| `list_crm_lead_contacts` | bitrix-crm | read | none | yes | no / no | Telegram-контакты лидов воронки «Партнёрская программа WB — индивидуальные условия»: какой username к какой сделке относится |
| `list_crm_pipelines` | bitrix-crm | read | none | yes | no / no | ВОРОНКИ CRM (сделки Bitrix): показать все воронки с их стадиями и количеством сделок |
| `list_dialog_errors` | communications-archive | read | none | no | no / no | Сбои в диалогах с агентом (таймаут, обрыв хода, внутренняя ошибка): что именно упало, когда, в каком диалоге, у какого пользователя, и разобран ли сбой |
| `list_drive_folder_items` | google-workspace | read | none | no | no / no | List direct contents of a Google Drive folder: files AND subfolders, with ids, names, mime types and links |
| `list_drive_folders` | google-workspace | read | none | no | no / no | List the company Google Drive folders a file can be uploaded to (top-level folders under the company root, e.g |
| `list_leader_evaluations` | management-reporting | read | none | yes | no / no | Read aggregated leader evaluations (how Артур, Наталья, Евгений, Сергей run their calls) across saved zoom reports in a date range |
| `list_my_automations` | agent-self-service | read | none | no | no / no | Показать автоматизации текущего агента. |
| `list_my_instructions` | agent-self-service | read | none | no | no / no | Показать личные инструкции и навыки текущего агента. |
| `list_overdue_tasks` | bitrix-tasks-and-chat | read | none | yes | no / no | Просроченные задачи Bitrix: дедлайн уже прошёл, задача не закрыта |
| `list_pending_owner_recommendations` | management-reporting | read | none | yes | no / no | List addressed manager recommendations from the current owner_daily_report for a given date |
| `list_pending_zoom_operational_dispatches` | zoom | read | none | yes | no / no | List Zoom calls that have a saved analytical report but were NOT yet dispatched as aggregated 'Итоги созвона' tasks to Bitrix |
| `list_periods` | management-reporting | read | none | yes | no / no | List recent dates available in chat messages and Zoom/owner sources. |
| `list_recommendations` | management-reporting | read | none | yes | no / no | List addressable recommendations with lifecycle status and optional event history |
| `list_recurring_tasks` | bitrix-tasks-and-chat | read | none | yes | no / no | Показать ПОВТОРЯЮЩИЕСЯ (регулярные) задачи — все или по одному человеку |
| `list_task_userfields` | bitrix-tasks-and-chat | read | none | yes | no / no | Показать ПОЛЬЗОВАТЕЛЬСКИЕ ПОЛЯ задач (UF_*), заведённые на портале, с кодами и подписями — чтобы использовать реальные коды в custom_fields инструментов create_bitrix_task / update_bitrix_task, а не угадывать |
| `list_telegram_agents` | telegram | read | none | yes | no / no | Telegram-агенты компании: встроенные (основной бот и аккаунт компании) и заведённые владельцем, плюс список тех, кому разрешено каждому писать |
| `list_telegram_chats` | telegram | read | none | yes | no / no | ЧАТЫ аккаунта компании @AlberyAIManager: каналы, группы и личные переписки, которые видит сам аккаунт — В ТОМ ЧИСЛЕ ЗАКРЫТЫЕ каналы и закрытые групповые чаты (у них нет ссылки t.me и нет публичного превью, поэтому больше |
| `list_telegram_contacts` | telegram | read | none | yes | no / no | Кому агент может писать в Telegram от лица аккаунта компании: справочник известных числовых id с @username и именами |
| `list_zoom_calls` | zoom | read | none | yes | no / no | List Zoom cloud recordings/calls with dates, technical topics, participants, and transcript segment counts. |
| `log_task_time` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Записать УЧЁТ ВРЕМЕНИ по задаче (затраченное время) |
| `make_sheet_applet` | google-workspace | write | none | no | yes / yes | Make a Google Sheet usable by an ANONYMOUS Apps Script web app WITHOUT any Google login or authorization |
| `manage_apps_script` | google-workspace | privileged-configuration | explicit | no | yes / yes | Google Apps Script via the Apps Script API |
| `manage_bitrix_department` | bitrix-tasks-and-chat | privileged-configuration | explicit | yes | yes / yes | ОТДЕЛЫ оргструктуры: create (name, опц |
| `manage_crm_deal_field` | bitrix-crm | privileged-configuration | explicit | yes | yes / yes | СОБСТВЕННЫЕ ПОЛЯ СДЕЛОК (UF_CRM_*): add — создать поле (label; type: string/integer/double/boolean/date/datetime/money/url/enumeration/employee/file/address; для enumeration обязателен list_items), update — поменять подп |
| `manage_crm_pipeline_stage` | bitrix-crm | privileged-configuration | explicit | yes | yes / yes | СТАДИИ воронки CRM: add — добавить стадию (name; опц |
| `move_drive_file_to_folder` | google-workspace | write | none | no | yes / yes | Move a Google Drive item — file, spreadsheet, document OR folder — into another Drive folder (folder id or URL) |
| `next_contract_number` | legal-contracts | write | none | no | yes / yes | НОМЕР ДОГОВОРА: свободный номер на сегодня по правилу «дата, затем дата-1, дата-2» (23.07.2026, 23.07.2026-1) |
| `notify_client_when_task_done` | bitrix-tasks-and-chat | write | explicit | yes | yes / yes | СКАЗАТЬ КЛИЕНТУ, КОГДА ЗАДАЧА ВЫПОЛНЕНА |
| `notify_iu_group` | communications-archive | write | explicit | no | yes / yes | Написать в группу Битрикса «Работа с ИУ» от лица Агента по работе с ИУ |
| `organize_drive_folder` | google-workspace | write | explicit | no | yes / yes | Smartly organize a Google Drive folder: create/reuse category subfolders and move files AND folders into categories |
| `preview_zoom_operational_tasks` | zoom | read | none | yes | no / no | Preview the aggregated 'Итоги созвона' Bitrix tasks that would be created for one Zoom call WITHOUT sending |
| `preview_zoom_participant_reports` | zoom | read | none | yes | no / no | Preview personal participant report Bitrix tasks for one Zoom call WITHOUT sending: one supportive task per matched participant, with shared call outcomes plus personal soft evaluation |
| `process_chat_ocr` | communications-archive | local-artifact-write | none | no | yes / yes | Run OCR processing for image/PDF chat attachments through the local app workflow |
| `read_google_doc` | google-workspace | read | none | no | no / no | Прочитать СУЩЕСТВУЮЩИЙ Google-документ по ссылке или id: output_format='text' — текст для ответа пользователю, output_format='html' — разметка для правки |
| `read_google_sheet_values` | google-workspace | read | none | no | no / no | Read a 2D array of cell values from an A1 range of a Google Sheet (value_render_option: FORMATTED_VALUE default \| UNFORMATTED_VALUE raw numbers \| FORMULA to inspect formulas) |
| `read_telegram_chat` | telegram | read | none | yes | no / no | СООБЩЕНИЯ одного чата Telegram глазами аккаунта @AlberyAIManager — закрытого канала и закрытой группы тоже |
| `remove_drive_item_from_folder` | google-workspace | destructive | explicit | no | yes / yes | Remove a Google Drive item — file, spreadsheet, document OR folder — from one specified parent folder without deleting the item from Drive completely |
| `reopen_bitrix_task` | bitrix-tasks-and-chat | write | explicit | yes | yes / yes | Reopen/renew one completed Bitrix task and write a comment explaining why |
| `report_overdue_discipline` | management-reporting | write | none | yes | yes / yes | Дисциплина сроков за период: кто просрачивал задачи и кому двигали дедлайны |
| `resolve_dialog_errors` | communications-archive | privileged-configuration | explicit | no | yes / yes | Снять метку «ОШИБКА» с диалога после разбора сбоя |
| `save_news_digest` | telegram | write | none | yes | yes / yes | Сохранить недельную новостную сводку, чтобы на повторные вопросы отвечать из неё, а не пересобирать |
| `save_owner_daily_report` | management-reporting | write | none | yes | yes / yes | Save a generated daily owner report directly to owner_daily_reports |
| `save_owner_weekly_report` | management-reporting | write | none | yes | yes / yes | Save a generated weekly owner report directly to owner_weekly_reports |
| `save_recommendation_event` | management-reporting | write | none | yes | yes / yes | Append one lifecycle event for an addressable recommendation and optionally update its status |
| `save_zoom_call_report` | zoom | write | none | yes | yes / yes | Save a generated AI report for one Zoom call directly to zoom_calls.analytical_note in PostgreSQL |
| `schedule_my_automation` | agent-self-service | write | none | no | no / no | Создать или обновить расписание собственной автоматизации. |
| `search_company_knowledge` | knowledge-and-documents | read | none | no | no / no | Search the persistent 'О компании' knowledge base, including Google Drive mirrored docs/sheets |
| `search_messages` | communications-archive | read | none | no | no / no | Search raw chat messages and OCR text from attached images for a period |
| `search_tasks` | bitrix-tasks-and-chat | read | none | yes | no / no | Search Bitrix tasks by id, period, text, or responsible user |
| `search_zoom_transcripts` | zoom | read | none | yes | no / no | Search Zoom transcript segments by text and optional date range |
| `send_bitrix_message` | bitrix-tasks-and-chat | write | explicit | yes | yes / yes | Send one personal Bitrix message to a single employee via the configured BITRIX_WEBHOOK_BASE account (your own user — there is no separate bot user) |
| `send_contract` | legal-contracts | write | explicit | no | yes / yes | ДОГОВОР КЛИЕНТУ: собирает готовый PDF по реквизитам из сделки (постоянный текст + реквизиты сторон), присваивает номер и отправляет клиенту в Telegram НА СОГЛАСОВАНИЕ |
| `send_owner_recommendations_to_bitrix` | bitrix-tasks-and-chat | write | explicit | yes | yes / yes | Create owner_daily_report recommendation TASKS in Bitrix from the configured BITRIX_WEBHOOK_BASE account (owner) — one task per recipient, NOT personal messages |
| `send_owner_weekly_report_pdf` | management-reporting | write | explicit | yes | yes / yes | Send the current weekly owner report as a PDF into Bitrix personal messages (default recipient: Evgeniy Palei, bitrix_user_id 1) |
| `send_telegram_message` | telegram | write | explicit | yes | yes / yes | Написать человеку в Telegram ОТ ЛИЦА аккаунта компании @AlberyAIManager (не от бота) |
| `send_terms` | legal-contracts | write | explicit | no | yes / yes | УСЛОВИЯ КЛИЕНТУ: отправляет текст условий ДОСЛОВНО из документа «Условия ИУ — текст для клиента» в базе знаний и сам добавляет вопрос «Есть вопросы по условиям?» |
| `set_agent_team` | agent-management | privileged-configuration | explicit | yes | yes / yes | Добавить/убрать людей из команды агента (кому он доступен) |
| `set_agent_tools` | agent-management | privileged-configuration | explicit | yes | yes / yes | Включить/выключить MCP-инструменты у агента |
| `set_telegram_access` | telegram | privileged-configuration | explicit | yes | yes / yes | Выдать или забрать доступ к Telegram-агенту по @username |
| `share_drive_item_for_everyone` | google-workspace | privileged-configuration | explicit | no | yes / yes | Open ANY Google Drive item — spreadsheet, document, folder, file, or an Apps Script project — for ANYONE WITH THE LINK (viewer by default) |
| `start_here_always_read_ai_instructions` | knowledge-and-documents | read | none | no | no / no | MANDATORY FIRST TOOL |
| `update_agent` | agent-management | privileged-configuration | explicit | yes | yes / yes | Изменить агента: имя/должность (синхронизируются с Bitrix), роль-промпт, вкл/выкл |
| `update_ai_capabilities` | runtime | privileged-configuration | explicit | no | yes / yes | Record/update the assistant's capabilities note when this tool is enabled |
| `update_bitrix_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Изменить существующую задачу Bitrix по точному bitrix_task_id — любой набор полей из конструктора задачи: соисполнители (accomplice_*), наблюдатели (auditor_*), теги, родительская задача (parent_task_id), проект (group_i |
| `update_crm_deal` | bitrix-crm | write | none | yes | yes / yes | Изменить СДЕЛКУ CRM: название, стадию (stage — движение по воронке), сумму, ответственного, комментарий, пользовательские поля; перенос в ДРУГУЮ воронку — category_id/pipeline_name (+опц |
| `update_crm_pipeline` | bitrix-crm | write | none | yes | yes / yes | Переименовать воронку CRM или поменять её порядок (sort) |
| `update_employee_dossier` | people-and-org | privileged-configuration | explicit | yes | yes / yes | Записать наблюдение в ДОСЬЕ сотрудника (внутреннее): паттерны его задач, что удалось/не удалось автоматизировать, как человек взаимодействует с агентом |
| `update_recurring_task` | bitrix-tasks-and-chat | write | none | yes | yes / yes | Изменить ПОВТОРЯЮЩУЮСЯ (регулярную) задачу: дни недели, время создания, дедлайн, название/описание/чек-лист/критерий результата |
| `upload_file_to_drive` | google-workspace | write | none | no | yes / yes | Upload a file the user SENT (by its attachment_id, format att_...) into a company Google Drive folder |
| `upsert_ai_instruction` | knowledge-and-documents | privileged-configuration | explicit | no | yes / yes | Create or update one editable AI instruction folder by path in Настройки -> Инструкции для ИИ. |
| `upsert_my_instruction` | agent-self-service | write | none | no | no / no | Создать или обновить личную самообученную инструкцию. |
| `workspace_add_lead_note` | client-funnel | write | none | yes | yes / yes | Записать комментарий по лиду — свободный текст о клиенте и общении |
| `workspace_get_conversation` | client-funnel | read | none | yes | no / no | Карточка одного обращения и его переписка: кто клиент, этап воронки, кто ведёт разговор, сколько ждёт ответа, и последние сообщения с состоянием доставки. |
| `workspace_list_conversations` | client-funnel | read | none | yes | no / no | Обращения клиентов в рабочем окне «Работа с воронками» (Telegram) |
| `workspace_list_lead_notes` | client-funnel | read | none | yes | no / no | Комментарии по лиду: что оператор и агент записали о клиенте и общении — договорённости, особенности, чего клиент ждёт |
| `workspace_list_urgent` | client-funnel | read | none | yes | no / no | Обращения, на которые никто не ответил дольше порога (по умолчанию 10 минут) |
| `workspace_reply` | client-funnel | write | none | yes | yes / yes | Отправить клиенту ответ в обращении |
| `workspace_set_control` | client-funnel | write | none | yes | yes / yes | Передать разговор человеку (mode=human), вернуть агенту (mode=ai) или приостановить ответы (mode=paused) |
| `workspace_set_stage` | client-funnel | write | none | yes | yes / yes | Перевести сделку обращения на другой этап воронки ИУ |
| `workspace_set_status` | client-funnel | write | none | yes | yes / yes | Закрыть обращение, вернуть его в работу или пометить спамом |
| `write_company_sheet` | google-workspace | write | explicit | no | yes / yes | Записать значения в разрешённую рабочую таблицу компании. |
| `write_google_sheet_values` | google-workspace | write | none | no | yes / yes | Write a 2D array of values/formulas into an A1 range of a Google Sheet (USER_ENTERED, so formulas work) |
