-- 065_funnel_scenarios.sql
-- Idempotent. Настраиваемый сценарий воронки: шаги этапов и выключатель агента.
--
-- Why: владелец 25.07.2026 — «сделаем инструмент „Работа с воронками“, внутри можно выбрать
-- воронку и сценарий настраивать в неё, чтобы этим можно было прям управлять». До этого шаги
-- этапов жили только в коде: любая правка формулировки требовала инженера и деплоя, а владелец
-- лучше всех знает, как разговаривать с его клиентами.
--
-- Что здесь настраивается: ТЕКСТ шага (чего агент ждёт от клиента и что делает) и включён ли
-- агент на воронке. Условия и приоритеты правил остаются в коде — они завязаны на факты и
-- проверены тестами; текст шага уходит в промпт и безопасен для правки владельцем.
--
-- Каждая правка пишется в историю: это промпт живого агента, и «кто и когда так решил» должно
-- быть видно без раскопок.
CREATE TABLE IF NOT EXISTS funnel_scenarios (
    id          bigserial PRIMARY KEY,
    funnel_id   int  NOT NULL,                    -- id воронки (категории сделок) в Битриксе
    stage_id    text NOT NULL DEFAULT '',         -- этап; '' = настройки воронки целиком
    trigger     text NOT NULL DEFAULT '',         -- когда этап наступает (для наглядности)
    need        text NOT NULL DEFAULT '',         -- чего агент ждёт от клиента
    action      text NOT NULL DEFAULT '',         -- что агент делает на этом этапе
    enabled     boolean NOT NULL DEFAULT TRUE,    -- для stage_id='': работает ли агент на воронке
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text NOT NULL DEFAULT ''
);
-- Одна настройка на этап: сохранение перезаписывает её, а не плодит копии.
CREATE UNIQUE INDEX IF NOT EXISTS uq_funnel_scenarios ON funnel_scenarios (funnel_id, stage_id);

-- История правок: что именно поменяли, с чего на что и кто.
CREATE TABLE IF NOT EXISTS funnel_scenario_history (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    funnel_id   int  NOT NULL,
    stage_id    text NOT NULL DEFAULT '',
    field       text NOT NULL,                    -- need | action | trigger | enabled
    old_value   text NOT NULL DEFAULT '',
    new_value   text NOT NULL DEFAULT '',
    author      text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_funnel_scenario_history ON funnel_scenario_history (funnel_id, id DESC);
