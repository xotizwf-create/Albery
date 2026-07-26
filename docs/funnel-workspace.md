# «Работа с воронками»: собственный Telegram-инбокс Albery

Страница `/agent-funnels` — отдельное рабочее место для обращений клиентов. Концепция интерфейса
и передачи диалога между ИИ и оператором взята из
[статьи на Habr](https://habr.com/ru/articles/988592/), но модель и бизнес-логика принадлежат
Albery: внешний API для генерации ответа не используется.

Документ описывает целевую архитектуру, безопасный ввод в эксплуатацию и откат. До завершения
ручного сквозного теста контур должен оставаться в режиме
`FUNNEL_WORKSPACE_ENABLED=0`, `FUNNEL_WORKSPACE_AI_ENABLED=0`.

## Что видит оператор

- Слева — обращения, источник, стадия, статус, непрочитанные сообщения и время последнего
  события.
- Справа — единая переписка клиента, оператора и агента с состоянием доставки каждого
  исходящего сообщения.
- Оператор может забрать диалог у агента, ответить и явно вернуть его агенту.
- Ответ оператора продлевает человеческое управление на 120 секунд. Если новых действий нет,
  управление автоматически возвращается агенту.
- Журнал можно искать по всей сохранённой переписке и выгружать в CSV с фильтрами по статусу,
  периоду и автору.
- Статус `unknown` означает, что Telegram мог принять сообщение до сетевого таймаута.
  Автоповтора нет: сначала нужно проверить живой чат Telegram, иначе клиент может получить
  дубль.
- Диалог связан со сделкой в категории 16. Стабильная личность клиента — числовой Telegram ID;
  `@username` используется только для отображения.

Telegram пока единственный источник, но транспорт вынесен в
`funnel_workspace_sources`: следующий источник сможет переиспользовать модель диалога и журнал.
Для него всё равно понадобятся отдельный transport adapter и небольшое подключение в UI.

## Границы системы

```
Telegram Business
       │ business_message / edited / deleted
       ▼
albery-tg.service — единственный getUpdates consumer
       │ сначала durable raw update
       ▼
PostgreSQL
  conversations ─ messages ─ control_events
        │              │
        ├─ ai_jobs     ├─ outbox ──────────→ Telegram Bot API
        │              └─ crm_actions
        ▼                    │
Hermes / ИУ runtime          └─────────────→ Bitrix, category 16

albery.service → /agent-funnels и JSON API → тот же журнал PostgreSQL
```

Критические инварианты:

1. Только `albery-tg.service` читает `getUpdates` и отправляет сообщения в Telegram.
   Веб-процесс не отправляет сообщения; защищённый media proxy делает только `getFile` и
   скачивание вложения для авторизованного оператора.
2. Входящий update фиксируется в PostgreSQL до продвижения polling offset.
3. Оператор и агент пишут в один outbox. Сетевой таймаут после отправки превращает доставку
   в `unknown`, а не в автоматический повтор.
4. Точный `business_connection_id` хранится в каждом диалоге; случайная подмена соединения
   запрещена.
5. Переход `ai → human → ai/paused` увеличивает `state_version`. Запоздавший ответ агента с
   прежней версией отбрасывается.
6. Старый прямой обработчик Telegram и Открытая линия не могут отвечать одновременно с новым
   контуром.
7. Текст клиента попадает только в `agent-iu-customer-runtime`: это отдельный активный
   Hermes-коннектор без Telegram/Bitrix bridge и ровно с нулём инструментов. Роль и тон
   берутся из профиля `agent-po-rabote-s-iu`; CRM и отправка выполняются детерминированным
   кодом, а не моделью.
8. Медленный Bitrix не блокирует Telegram: создание/поиск сделки, смена стадии и последствия
   подтверждённой доставки идут через отдельную retryable CRM-очередь.

Девять таблиц контура создаёт миграция `070_funnel_workspace.sql`:

- `funnel_workspace_sources`;
- `funnel_workspace_conversations`;
- `funnel_workspace_messages`;
- `funnel_workspace_control_events`;
- `funnel_workspace_updates`;
- `funnel_workspace_ai_jobs`;
- `funnel_workspace_outbox`;
- `funnel_workspace_crm_actions`;
- `funnel_workspace_settings`.

Миграция аддитивная. Удалять таблицы при откате нельзя: в них остаются журнал и неразобранные
очереди.

## Управление и дедлайны

| Режим | Кто отвечает | Переход |
|---|---|---|
| `ai` | агент ИУ | по умолчанию только для разрешённого тестового Telegram ID |
| `human` | оператор | кнопка «Забрать», ответ оператора или исходящее владельца в Telegram |
| `paused` | никто автоматически | эскалация, запрет allowlist, ошибка или ручная пауза |

`FUNNEL_WORKSPACE_HUMAN_LEASE_SECONDS=120` задаёт временную аренду оператором.
`FUNNEL_WORKSPACE_REPLY_WINDOW_HOURS=24` задаёт локальный защитный дедлайн ответа Telegram
(допустимо 1–48 часов). Истёкший дедлайн блокирует outbox и виден оператору.

ИИ включается двумя независимыми воротами:

- `FUNNEL_WORKSPACE_AI_ENABLED=1`;
- `FUNNEL_WORKSPACE_AI_ALLOW_IDS` содержит положительный числовой Telegram ID клиента.

Пустой allowlist означает «никому». `*` означает «всем» и разрешён только после успешного
тестового периода и явного решения владельца. Опечатки в allowlist блокирует deploy smoke.

## Отдельный вход

`/agent-funnels` не требует сессии основного кабинета. У страницы собственная cookie-сессия,
CSRF-проверка, same-origin защита и rate limit. На входе оператор указывает своё имя и общий
пароль; имя фиксируется в сообщениях и событиях управления.

Пароль хранится только как Werkzeug `scrypt`/`pbkdf2` hash. Не записывать открытый пароль в
`.env`, shell history, задачу или лог. Первичная настройка выполняется так:

1. Войти в основной кабинет как администратор.
2. Открыть `/agent-funnels`.
3. В форме первичной настройки подтвердить текущий пароль администратора и дважды ввести новый
   пароль рабочего места (не короче 12 символов).
4. Выйти из основного кабинета или открыть приватное окно и проверить самостоятельный вход.

Hash сохраняется в `funnel_workspace_settings`; `FUNNEL_WORKSPACE_PASSWORD_HASH` нужен только
как аварийный конфигурационный источник. Сессия рабочего места не даёт прав администратора
основного сайта.

## Переменные окружения

| Переменная | Безопасное начальное значение | Назначение |
|---|---:|---|
| `FUNNEL_WORKSPACE_ENABLED` | `0` | маршрутизация Telegram в новый журнал и UI |
| `FUNNEL_WORKSPACE_AI_ENABLED` | `0` | запуск ответов агента |
| `FUNNEL_WORKSPACE_AI_ALLOW_IDS` | пусто | тестовый allowlist; пусто = никто |
| `FUNNEL_WORKSPACE_AGENT_SLUG` | `agent-po-rabote-s-iu` | профиль роли и тона |
| `FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG` | `iu-customer-runtime` | нулевой клиентский коннектор |
| `FUNNEL_WORKSPACE_HUMAN_LEASE_SECONDS` | `120` | пауза агента после оператора |
| `FUNNEL_WORKSPACE_REPLY_WINDOW_HOURS` | `24` | дедлайн ответа, 1–48 часов |
| `FUNNEL_WORKSPACE_RETENTION_DAYS` | `90` | срок хранения перед регламентной очисткой |
| `FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD` | пусто | опциональный отдельный UF с numeric Telegram ID |
| `OPENLINE_AGENT_ENABLED` | `0` | старый агент Открытой линии обязан быть выключен |
| `TG_BUSINESS_AUTOREPLY` | `0` | старый прямой Telegram-ответчик обязан быть выключен |
| `CANONICAL_WEB_HOST` | реальный host | защита cookie/origin для отдельного входа |
| `AGENT_MCP_PUBLIC_BASE` | реальный HTTPS base | точный публичный host закрытого Hermes connector |

Не придумывать значение `FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD`. Пока отдельный UF не создан
и не подтверждён в Bitrix, поле остаётся пустым, а сделка восстанавливается по стабильному
маркеру `[tg:<numeric-id>]`. Если UF задан, он обязан отличаться от legacy
`CRM_TELEGRAM_FIELD`, где исторически лежал username.

## Безопасный ввод в эксплуатацию

### 1. Локальные ворота

Работать только из чистой ветки, основанной на актуальном `origin/main`. До отправки:

```text
python -m pyflakes scripts/deploy_smoke.py funnel_workspace.py \
  funnel_workspace_store.py funnel_workspace_crm.py funnel_telegram_gateway.py
python -m pytest
cd Интерфейс
npm ci
npm run lint
npm run build
```

Сборка UI выполняется локально, не на прод-сервере с 2 ГБ RAM. Архив `dist` должен быть собран
из того же commit SHA, который прошёл CI; сохранить SHA и checksum рядом с артефактом.
Далее: commit → push → зелёный CI → только потом прод.

### 2. Бэкапы до изменения

На сервере сначала проверить свободную память, чистый git и текущие версии сервисов. Затем:

- создать tar-архив затрагиваемого кода и git tag/commit-точку отката;
- скопировать `.env` в файл с timestamp и правами `600`;
- скопировать `/root/.hermes/config.yaml` с timestamp и правами `600`;
- сделать `pg_dump` таблицы `agents` и существующих девяти `funnel_workspace_*` таблиц
  (при первом запуске 070 их ещё нет — сохранить как минимум schema dump всей БД);
- проверить, что архивы непустые и читаются. Не печатать DSN, токены или содержимое `.env`.

Если любой бэкап нельзя создать или проверить, деплой останавливается.

### 3. Спящий деплой

1. На сервере выполнить только `git pull --ff-only` уже проверенного commit.
2. Проверить изменённые Python-файлы через `python -m py_compile`.
3. Применить аддитивную миграцию:

   ```text
   .venv/bin/python scripts/ensure_postgres.py
   ```

4. Создать/проверить закрытый клиентский коннектор:

   ```text
   .venv/bin/python scripts/ensure_workspace_customer_connector.py
   ```

   Скрипт сам делает резервную копию Hermes config, не выводит token и отказывается
   переиспользовать агента с Telegram/Bitrix bridge.
5. Обновить `.env` загруженным безопасным скриптом, затем `chmod 600`. На этом этапе:

   ```text
   FUNNEL_WORKSPACE_ENABLED=0
   FUNNEL_WORKSPACE_AI_ENABLED=0
   FUNNEL_WORKSPACE_AI_ALLOW_IDS=
   OPENLINE_AGENT_ENABLED=0
   TG_BUSINESS_AUTOREPLY=0
   ```

6. Распаковать локально собранный frontend рядом с текущим каталогом, сверить checksum и
   наличие `index.html`, затем сделать короткий same-filesystem swap
   `Интерфейс/dist.next-<sha> → Интерфейс/dist`. Старый каталог сохранить как
   `dist.bak-<timestamp>`; собирать npm на сервере запрещено.

### 4. Рестарт только в пустое окно

До каждого рестарта оба запроса должны вернуть `0`:

```sql
SELECT count(*) FROM bitrix_inflight_turns;
SELECT count(*) FROM agent_automations WHERE last_status = 'running';
```

Если работа идёт, ждать и перепроверять до шести минут; не убивать живой ход. После
настройки коннектора нужны:

- `albery.service` — новый backend/API;
- `albery-tg.service` — durable Telegram worker;
- `hermes-gateway.service` — новый customer connector.

Рестартовать только действительно изменившийся сервис и сразу проверять `is-active`.
Для `hermes-gateway` дополнительно убедиться, что нет активного диалога владельца.

После рестартов:

```text
cd /var/www/albery
.venv/bin/python scripts/deploy_smoke.py
journalctl -u albery.service -p err --since "15 minutes ago"
journalctl -u albery-tg.service -p err --since "15 minutes ago"
journalctl -u hermes-gateway.service -p err --since "15 minutes ago"
```

Smoke проверяет девять таблиц, маршруты, worker-сервисы, password hash, Telegram
`can_reply`, взаимоисключение старых ответчиков, allowlist и соответствие DB token закрытому
Hermes connector. URL и token в вывод не попадают.

### 5. Пароль и Telegram Business

1. Пока workspace выключен, задать отдельный пароль через административную форму и проверить
   вход из приватного окна.
2. В Telegram открыть настройки Business → чат-боты, переподключить Albery и выдать право
   отвечать. Это ручное действие владельца Telegram; код не может выдать право сам.
3. Дождаться нового `business_connection` update и проверить в состоянии транспорта
   `enabled=true`, `can_reply=true`. Идентификатор соединения в задачу или лог не копировать.
4. Только после этого включить `FUNNEL_WORKSPACE_ENABLED=1`, оставить ИИ выключенным,
   дождаться пустого окна и перезапустить `albery-tg.service`.
5. Повторить deploy smoke. Красный smoke означает немедленную остановку теста.

### 6. Сквозной тест одним ID

Использовать заранее согласованный тестовый Telegram ID, не живых клиентов:

1. Клиент пишет в личку; в UI сразу появляется обращение и сделка category 16.
2. В UI видны входящее сообщение, numeric-ID-связка и чат.
3. Оператор забирает диалог и отвечает. В Telegram приходит только текст — без имени
   сотрудника/агента в начале.
4. В UI ответ имеет состояние `sent` и реальный provider message ID. Повторная доставка update
   не создаёт дубль.
5. Проверить возврат агенту вручную и автоматическое возвращение через 120 секунд.
6. Проверить закрытие/возобновление, непрочитанные, фильтр и привязку сделки.
7. Проверить ошибку доставки и `unknown`: автоматического повтора быть не должно.

После успешного human-only теста:

```text
FUNNEL_WORKSPACE_AI_ENABLED=1
FUNNEL_WORKSPACE_AI_ALLOW_IDS=<один согласованный numeric Telegram ID>
```

Перезапустить `albery-tg.service` только в пустое окно, повторить smoke и тот же сценарий.
Проверить, что ответ агента виден в UI, ручной захват отменяет незавершённый ответ, а после
возврата отвечает именно агент ИУ. Расширять список или ставить `*` можно только отдельным
решением после периода наблюдения.

## Контроль очередей

До рестарта, после smoke и после каждого тестового диалога проверить:

```sql
SELECT processing_status, count(*)
  FROM funnel_workspace_updates
 GROUP BY processing_status
 ORDER BY processing_status;

SELECT processing_status, count(*)
  FROM funnel_workspace_ai_jobs
 GROUP BY processing_status
 ORDER BY processing_status;

SELECT delivery_status, count(*)
  FROM funnel_workspace_outbox
 GROUP BY delivery_status
 ORDER BY delivery_status;

SELECT processing_status, count(*)
  FROM funnel_workspace_crm_actions
 GROUP BY processing_status
 ORDER BY processing_status;
```

Ненулевая очередь `processing`/`leased`/`sending` перед рестартом — причина ждать. Растущие
`retry`, `failed`, `dead_letter` или `unknown` — причина остановить rollout и разобрать
конкретные строки, не делать массовый повтор. После проверки читать свежие журналы всех трёх
сервисов.

## Точная граница первой версии

- В inbox попадают новые личные переписки Telegram Business после подключения бота. Это не
  импорт старой истории, и обычный чат пользователя непосредственно с ботом не считается
  клиентским Business-диалогом.
- Один Telegram Business chat пока соответствует одной постоянной карточке обращения.
  Повторное сообщение после закрытия переоткрывает её; отдельные сессии обращений — следующий
  этап аналитики.
- Первая версия принимает и показывает вложения, но оператор отправляет из UI только текст.
- Общий пароль рассчитан на одну операторскую роль. Персональные аккаунты и аудит прав нужны
  до подключения нескольких менеджеров.

## Откат

Сначала остановить новый исходящий трафик:

```text
FUNNEL_WORKSPACE_AI_ENABLED=0
FUNNEL_WORKSPACE_ENABLED=0
OPENLINE_AGENT_ENABLED=0
TG_BUSINESS_AUTOREPLY=0
```

Не включать старый ответчик автоматически: иначе два контура могут ответить одному клиенту.
Перед рестартом дождаться отсутствия `leased`/`processing`/`sending` и общих inflight-ходов.
Затем перезапустить только `albery-tg.service` и повторить smoke.

Если нужен кодовый откат, создать обычный revert-коммит, прогнать локальные ворота и CI,
затем `git pull --ff-only`; не применять `git reset --hard` на сервере. UI вернуть
same-filesystem swap на сохранённый `dist.bak-<timestamp>`. `.env` и Hermes config можно
восстановить только из проверенных резервных копий с правами `600`.

Миграцию 070 назад не откатывать и данные не удалять. Они нужны для расследования, повторного
включения и ручной обработки уже полученных обращений. После отката зафиксировать состояние
очередей и причину в инженерном журнале.
