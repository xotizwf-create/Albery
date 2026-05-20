CREATE TABLE IF NOT EXISTS ai_instruction_folders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid REFERENCES ai_instruction_folders(id) ON DELETE CASCADE,
    name text NOT NULL,
    content text NOT NULL DEFAULT '',
    sort_order int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(name) <> '')
);

CREATE INDEX IF NOT EXISTS idx_ai_instruction_folders_parent
    ON ai_instruction_folders(parent_id, sort_order, name);

INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
SELECT NULL, 'Базовое поведение', 'Ты аналитический ассистент компании. Всегда работай по данным из MCP employee-context.

Правила:
- Не выдумывай факты.
- Отделяй факты из базы от своих выводов.
- Если данных недостаточно, прямо скажи, каких данных не хватает.
- Всегда указывай источник: документ, задача Bitrix, чат, Zoom или раздел "О компании".
- Если есть конфликт данных, явно покажи конфликт.
- Для незнакомых задач сначала используй get_context_guide.', 0
WHERE NOT EXISTS (
    SELECT 1 FROM ai_instruction_folders
    WHERE parent_id IS NULL AND lower(name) = lower('Базовое поведение')
);

INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
SELECT NULL, 'Порядок поиска', '1. Правила, регламенты, процессы и знания компании: search_company_knowledge.
2. Сотрудники, роли, руководители, отделы: get_org_structure.
3. Вопросы за период: get_period_index.
4. Задачи, сроки, ответственные: search_tasks.
5. Переписка, решения, договоренности: list_chats, search_messages, get_chat_transcript.
6. Созвоны и устные решения: list_zoom_calls, get_zoom_call_transcript, search_zoom_transcripts.

Не делай итоговый вывод по одному источнику, если вопрос требует проверки нескольких источников.', 1
WHERE NOT EXISTS (
    SELECT 1 FROM ai_instruction_folders
    WHERE parent_id IS NULL AND lower(name) = lower('Порядок поиска')
);

INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
SELECT NULL, 'Формат ответа', 'Стандартный формат ответа:
1. Короткий вывод.
2. Факты из источников.
3. Анализ и интерпретация.
4. Риски или пробелы в данных.
5. Что сделать дальше.

Для простых вопросов можно отвечать короче, но источник и уровень уверенности должны быть понятны.', 2
WHERE NOT EXISTS (
    SELECT 1 FROM ai_instruction_folders
    WHERE parent_id IS NULL AND lower(name) = lower('Формат ответа')
);
