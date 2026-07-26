-- Состояние диалогов открытой линии: кто сейчас ведёт разговор — бот или человек.
--
-- Битрикс не умеет отдавать диалог ОБРАТНО боту: imopenlines.operator.transfer принимает только
-- id сотрудника или queue<ID>. Поэтому бот из чата не выходит никогда, а «кто ведёт» держим мы:
-- человек написал в диалог — бот замолкает, человек вернул командой — бот снова отвечает.
CREATE TABLE IF NOT EXISTS openline_dialogs (
  chat_id            bigint PRIMARY KEY,
  line_id            integer,
  connector          text,
  client_user_id     bigint,
  deal_id            integer,
  session_id         bigint,
  -- Ведёт ли разговор бот. FALSE = управление у человека, бот молчит и только журналит.
  bot_active         boolean     NOT NULL DEFAULT TRUE,
  -- Кто последним менял управление и почему — чтобы в разборе было видно, кто перехватил.
  control_by         bigint,
  control_reason     text,
  control_changed_at timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS openline_dialogs_deal_idx ON openline_dialogs (deal_id);
CREATE INDEX IF NOT EXISTS openline_dialogs_active_idx ON openline_dialogs (bot_active);
