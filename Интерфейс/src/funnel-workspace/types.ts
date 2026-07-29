export type ControlMode = "ai" | "human" | "paused";

export type ConversationStatus =
  | "new"
  | "in_progress"
  | "waiting"
  | "qualified"
  | "won"
  | "lost"
  | string;

export interface WorkspaceSession {
  authenticated: boolean;
  configured: boolean;
  csrf_token?: string;
  can_configure?: boolean;
  admin_session?: boolean;
  operator_name?: string | null;
  /** Имя, закреплённое за паролем рабочего окна: под ним и работает смена. */
  configured_operator_name?: string | null;
  workspace_enabled?: boolean;
}

export interface WorkspaceStatusOption {
  id?: string;
  value?: string;
  name?: string;
  label?: string;
  color?: string;
}

export interface WorkspaceMeta {
  source?: string;
  source_name?: string;
  telegram_connected?: boolean;
  ai_enabled?: boolean;
  ai_rollout_limited?: boolean;
  human_lease_seconds?: number;
  /** Через сколько минут без ответа обращение считается срочным. */
  urgent_after_minutes?: number;
  statuses?: WorkspaceStatusOption[];
  /** Этапы воронки ИУ в порядке владельца — приходят из iu_funnel.CHAIN. */
  funnel_stages?: Array<{ value: string; label: string; goal?: string; order?: number }>;
  bitrix_base_url?: string;
}

export interface Conversation {
  id: number | string;
  external_user_id?: number | string | null;
  display_name: string;
  username: string | null;
  source: string;
  status: ConversationStatus;
  control_mode: ControlMode;
  control_mode_internal?: ControlMode;
  resume_at: string | null;
  reply_deadline_at?: string | null;
  can_reply?: boolean;
  ai_available?: boolean;
  state_version: number;
  unread_count: number;
  last_message: string | null;
  last_message_at: string | null;
  deal_id: number | null;
  deal_title: string | null;
  stage_id: string | null;
  /** С какого момента клиент ждёт ответа; null — ответ уже дан. */
  awaiting_reply_since?: string | null;
  /** «urgent» — клиент ждёт ответа дольше порога; считает сервер. */
  urgency?: "urgent" | "working" | string;
  /** Рабочий статус: new_client | client_waiting | waiting_client. Считает сервер. */
  work_state?: string;
  work_state_label?: string;
  urgent?: boolean;
  waiting_minutes?: number | null;
  /** Место в очереди разбора: 1 — очень срочно, 4 — ждём ответа от клиента. */
  priority?: number;
  /** Третий бейдж: «ИИ управляет» / «Человек управляет» / «Ответы приостановлены». */
  control_label?: string;
  /** Диалог забран человеком насовсем: ИИ сам не вернётся. */
  control_permanent?: boolean;
  /** Клиент явно нажал «Позвать оператора», а менеджер ещё не ответил. */
  manager_requested?: boolean;
  manager_requested_at?: string | null;
  /** Постоянный адрес обращения: /agent-funnels/<id>. */
  url?: string;
  stage_name: string | null;
  assigned_to: string | null;
}

export type MessageAuthorType = "client" | "ai" | "human" | "system" | string;
export type MessageDirection = "incoming" | "outgoing" | string;

export interface ConversationAttachment {
  media_type: string;
  file_name: string;
  mime_type: string;
  file_size: number | null;
  url: string;
  download_url: string;
}

/** Файл, загруженный оператором и ещё не отправленный клиенту. */
export interface OutgoingUpload {
  token: string;
  file_name: string;
  mime_type: string;
  file_size: number;
}

export interface ConversationMessage {
  id: number;
  created_at: string;
  author_type: MessageAuthorType;
  author_name: string | null;
  direction: MessageDirection;
  text: string;
  delivery_status: string | null;
  error: string | null;
  idempotency_key?: string | null;
  optimistic?: boolean;
  attachment?: ConversationAttachment | null;
}

export interface ConversationsPayload {
  conversations: Conversation[];
  total: number;
  limit: number;
  offset: number;
}

export interface MessagesPayload {
  messages: ConversationMessage[];
  next_after_id?: number | null;
  next_before_id?: number | null;
  has_more_before?: boolean;
}

/** Комментарий по лиду: свободная заметка оператора или агента о клиенте и общении.
    Дублируется в ленту сделки Битрикса; in_bitrix=false — у нас есть, туда не доехало. */
export interface LeadNote {
  id: number;
  author_type: "operator" | "agent" | string;
  author_name: string;
  text: string;
  bitrix_mirrored: boolean;
  bitrix_error?: string | null;
  created_at: string;
}
