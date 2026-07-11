-- 049: Offers on agent-created tasks (Р·Р°РґР°С‡Р° 1300 + РІР»Р°РґРµР»РµС† 2026-07-11): right after the agent
-- creates a task, the most suitable agent posts an offer-to-help comment; the responsible replies
-- in the comments WITHOUT naming the agent вЂ” the mention handler falls back to the open offer of
-- that task and routes the reply to the offering agent. One offer row per task; state:
-- 'open' (dialog continues) | 'declined' (a short В«РЅРµС‚В» reply closes it, no more auto-routing).
-- Idempotent (IF NOT EXISTS). Registered in ALWAYS_APPLY_MIGRATIONS.

CREATE TABLE IF NOT EXISTS bitrix_task_agent_offers (
    task_id        bigint PRIMARY KEY,
    agent_slug     text,              -- NULL = main agent
    agent_name     text,
    bot_id         bigint,            -- bot that posted the offer (replies come from it too)
    responsible_id integer,
    creator_id     integer,
    state          text NOT NULL DEFAULT 'open',
    offered_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
