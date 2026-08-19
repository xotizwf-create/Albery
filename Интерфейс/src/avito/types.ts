export type AvitoSessionStatus = "unknown" | "ok" | "needs_login" | "blocked" | "error";

export type AvitoAccount = {
  slug: string;
  label: string;
  egress_label: string;
  session_status: AvitoSessionStatus;
  session_checked_at?: string | null;
  last_error?: string;
  is_active: boolean;
};

export type AvitoChannelState = {
  transport_enabled: boolean;
  accounts: AvitoAccount[];
  total_conversations: number;
  unread_conversations: number;
};

export type AvitoListing = {
  id: string;
  title: string;
  url: string;
  price: string;
};

export type AvitoConversation = {
  id: number;
  account_slug: string;
  external_chat_id: string;
  external_user_id: number | null;
  username: string;
  display_name: string;
  status: "new" | "open" | "waiting" | "closed" | "spam" | "expired";
  control_mode: "ai" | "human" | "paused";
  unread_count: number;
  last_read_message_id: number;
  state_version: number;
  last_message_at: string | null;
  last_message_text: string;
  last_author_type: string;
  created_at: string | null;
  listing: AvitoListing;
};

export type AvitoMessage = {
  id: number;
  author_type: "client" | "agent" | "operator" | "system";
  author_name: string;
  direction: "inbound" | "outbound" | "system";
  text: string;
  delivery_status: "pending" | "sent" | "failed" | "unknown" | "cancelled";
  error_detail: string;
  occurred_at: string | null;
  sent_at: string | null;
};

export type AvitoConversationsPayload = {
  conversations: AvitoConversation[];
  total: number;
  unread: number;
  limit: number;
  offset: number;
};

export type AvitoMessagesPayload = {
  conversation: AvitoConversation;
  messages: AvitoMessage[];
};
