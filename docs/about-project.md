# О проекте Albery (about-project)

> Это «зерно» общего описания проекта. Пока здесь подробно задокументирована
> последняя крупная надстройка (оценка руководителей по Zoom-созвонам, 2026-06).
> Дальше документ нужно дособрать до полного обзора всего проекта (Zoom, чаты,
> owner-отчёты, Bitrix-интеграции, Hermes, инфраструктура).

Albery — система управленческой аналитики: подтягивает Zoom-созвоны, чаты и
Bitrix-задачи, строит по ним ИИ-отчёты и ставит задачи в Битрикс. Ассистент
**Hermes** работает 24/7 в Telegram (на прод-сервере, systemd `hermes-gateway`),
подключён к MCP-серверу `albery` и действует по «живым» AI-инструкциям из БД.

- Прод: `root@186.246.7.32`, проект `/var/www/albery`, домены `www.m4s.ru` / `mcp.m4s.ru`.
- Подробности по инфраструктуре, деплою и cron — в [agent.md](../agent.md).

---

## Надстройка: Оценка руководителей по Zoom-созвонам (2026-06)

### Зачем
По каждому созвону, помимо выделения задач, нужно:
1. оценивать, как **руководители** (Артур Степанян, Наталья Горюнова, Евгений
   Палей, Сергей Виноградов) ведут созвон — держат ли планку, соответствует ли
   встреча регламенту, не уходит ли разговор в сторону;
2. отправлять каждому участнику **краткую выжимку** созвона, руководителям — ещё
   и персональную **оценку**, а тем, у кого есть задачи, — список задач;
3. раз в неделю собирать собственнику (Евгению) **свод по руководителям**.

### Как это работает (поток данных)
```
Zoom-созвон → транскрипт → ИИ-отчёт (контракт zoom_processing v9)
   → сохранение в zoom_calls.analytical_note + raw_json.ai_report.analysis
   → Hermes показывает владельцу ПОЛНЫЙ отчёт + превью рассылки в Telegram
   → на «ставь» диспетчер создаёт по одной задаче «Итоги созвона» на участника
   → раз в неделю (ср 19:00) Hermes собирает свод руководителей → задача Евгению
```

### 1) Контракт отчёта `zoom_processing` v9
Источник правды в git: [scripts/zoom_processing_prompt_v9.md](../scripts/zoom_processing_prompt_v9.md),
патчер: [scripts/update_zoom_processing_prompt_leader_eval.py](../scripts/update_zoom_processing_prompt_leader_eval.py)
(деактивирует активную версию в `ai_prompts` и вставляет v9; есть `--dry-run`).

Добавлено поверх v8 (всё — надстройка, старое поведение сохранено):
- `dispatch_summary` — короткая выжимка для рассылки в формате **«Обсуждали / Решили»**,
  без блока «Соответствие регламенту» и без внутренних вердиктов;
- у участников `is_leader` и `role_on_call` (host / co_leader / participant);
- новый раздел **«12. Оценка руководителей»** + JSON `leader_evaluations`
  (`role`, `verdict` = good/minor_issue/issue, `result_for_owner` — для свода
  собственнику, `message_for_leader` — личный текст в задачу, тон наставника);
- правило операционных задач: **ответственный = только участник звонка**; задача
  про отсутствующего переадресуется участнику-инициатору с делегированием
  (`delegate_to`); участник из тех-логов без сопоставления с оргструктурой не
  отбрасывается молча, а подсвечивается на согласовании.

**Пятничная логика не изменилась:** «Итоги недели: Наталья и Артур»
(`weekly_review`) работают как раньше, только раздел перенумерован 13 → 14.

### 2) Диспетчер (раздача в Bitrix) — [app.py](../app.py)
Диспетчер берёт сохранённый отчёт и создаёт по одной агрегированной задаче
«Итоги созвона ЧЧ:ММ» на человека, дедлайн = дата созвона 19:00 МСК.

Новые/изменённые функции:
- хелперы чтения отчёта: `format_zoom_dispatch_summary`, `zoom_call_participants`,
  `zoom_call_leader_evaluations`, `resolve_zoom_recipient`,
  `find_zoom_leader_evaluation`, `build_zoom_card_description`;
- `build_zoom_operational_task_cards` — карточка **на каждого участника** (+ любого
  ответственного): выжимка → оценка (руководителям) → задачи или заглушка; отдаёт
  `unmatched_assignees` и `unmatched_participants`;
- `build_zoom_operational_tasks_dispatch` — больше не падает при нуле задач (есть
  заглушки), пустые карточки отсеивает;
- `preview_zoom_operational_tasks` / `dispatch_zoom_operational_tasks` — прокидывают
  `dispatch_summary`, `participants`, `leader_evaluations`, `unmatched_participants`;
- интро в задаче: «Также прошу ознакомиться со списком…».

Совместимость: старые v8-отчёты раздаются как раньше (задачи без выжимки/оценки).

### 3) Свод собственнику по средам — [app.py](../app.py) + MCP
- `collect_leader_evaluations_for_period` / `list_leader_evaluations` — агрегируют
  `leader_evaluations` за период, группируют по руководителю (счётчики
  good/minor_issue/issue для динамики);
- `leader_evaluations_window` — дефолтное окно «прошлая пятница … сегодня»
  (пт + пн + вт + ср);
- `dispatch_leader_evaluations_digest` — ставит Евгению **одну** задачу
  «Ознакомиться с оценкой руководителей за период …», дедлайн = завтра 10:00 МСК.

MCP (`mcp/context_server.py`, версия **0.9.0**), новые инструменты основного
коннектора (НЕ в FAQ): `list_leader_evaluations`, `dispatch_leader_evaluations_digest`.

### 4) Оркестрация Hermes (промпты + AI-инструкции, git)
- [scripts/hermes_zoom_to_tasks_prompt.txt](../scripts/hermes_zoom_to_tasks_prompt.txt)
  — теперь Hermes **перед каждой отправкой шлёт владельцу ПОЛНЫЙ отчёт** по созвону
  + превью «Что уйдёт в Битрикс» по каждому получателю, и только потом «Отправляем?».
- [scripts/ai_instruction_zoom_approval.md](../scripts/ai_instruction_zoom_approval.md)
  — поведение на «ставь» под новый формат.
- [scripts/hermes_leader_digest_prompt.txt](../scripts/hermes_leader_digest_prompt.txt)
  — новый промпт среды 19:00: читает `list_leader_evaluations`, собирает живой свод
  по руководителям (вывод + динамика + эпизоды), показывает владельцу; молчит, если
  оценок за период нет.
- [scripts/ai_instruction_leader_digest_approval.md](../scripts/ai_instruction_leader_digest_approval.md)
  — на «отправляй» → `dispatch_leader_evaluations_digest`.

### Кто такие руководители
Артур Степанян (29039), Наталья Горюнова (30237), Евгений Палей (1, собственник —
он же получатель свода), Сергей Виноградов (31157). Сопоставление имён из созвонов
с оргструктурой — через редактируемую инструкцию «Сопоставление имён сотрудников
(алиасы оргструктуры)» (например, `Анастасия Докучаева = Анастасия Андрусяк`).

### Согласование (review-gate)
Единый gate в Telegram: и по созвонам (выжимки + оценки + задачи), и по
четверговому своду — отправка в Битрикс только после явного ответа владельца.

### Чек-лист деплоя
1. `python scripts/update_zoom_processing_prompt_leader_eval.py` (на проде; сначала `--dry-run`).
2. Выкатить код: push в `main` → на проде `./scripts/update_server.sh` (билд + рестарт `albery`).
3. Рестарт `hermes-gateway` (новые MCP-инструменты) + `/reset` в Telegram.
4. Залить AI-инструкции через `scripts/upsert_albery_ai_instruction.py`:
   обновлённую `ai_instruction_zoom_approval.md` и новую `ai_instruction_leader_digest_approval.md`.
5. Залить промпты Hermes в `/root/.hermes/scripts/` (zoom-to-tasks + leader-digest).
6. Создать cron среды 19:00 МСК на leader-digest (по образцу owner-daily).

### Что ещё не сделано
- Cron leader-digest (ср 19:00) — требует создания на проде.
- Playbook в `docs/playbooks/` + индекс в `agent.md`.
