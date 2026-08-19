import type {
  AvitoAccount,
  AvitoChannelState,
  AvitoConversation,
  AvitoConversationsPayload,
  AvitoMessagesPayload,
} from "./types";

const API_ROOT = "/api/agent-center/avito";

export class AvitoApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "AvitoApiError";
    this.status = status;
    this.code = code;
  }
}

const messageFor = (payload: Record<string, unknown> | null, status: number): string => {
  const raw = payload?.error;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (status === 401) return "Сессия кабинета завершилась. Войдите снова.";
  if (status === 403) return "Недостаточно прав для этого действия.";
  if (status === 404) return "Не найдено.";
  if (status >= 500) return "Сервис временно недоступен.";
  return `Ошибка запроса (${status}).`;
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(init.headers || {}),
      },
    });
  } catch {
    throw new AvitoApiError("Нет связи с сервером. Проверьте подключение.", 0);
  }
  const raw = await response.text();
  let body: unknown = {};
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      throw new AvitoApiError(`Сервер вернул некорректный ответ (${response.status}).`, response.status);
    }
  }
  if (!response.ok) {
    const payload = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
    const code = typeof payload?.code === "string" ? payload.code : null;
    throw new AvitoApiError(messageFor(payload, response.status), response.status, code);
  }
  return body as T;
}

export const avitoApi = {
  getState: () => request<AvitoChannelState>("/state"),

  createAccount: (payload: { slug: string; label: string; egress_label?: string }) =>
    request<{ account: AvitoAccount }>("/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  setAccountActive: (slug: string, isActive: boolean) =>
    request<{ account: AvitoAccount }>(`/accounts/${encodeURIComponent(slug)}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    }),

  getConversations: (params: { account?: string; status?: string; q?: string; limit?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.account) search.set("account", params.account);
    if (params.status && params.status !== "all") search.set("status", params.status);
    if (params.q?.trim()) search.set("q", params.q.trim());
    search.set("limit", String(params.limit || 100));
    return request<AvitoConversationsPayload>(`/conversations?${search.toString()}`);
  },

  getMessages: (conversationId: number, afterId = 0) => {
    const search = new URLSearchParams();
    if (afterId > 0) search.set("after_id", String(afterId));
    const query = search.size ? `?${search.toString()}` : "";
    return request<AvitoMessagesPayload>(`/conversations/${conversationId}/messages${query}`);
  },

  reply: (
    conversationId: number,
    payload: { text: string; expected_version: number; operator_name: string; idempotency_key: string },
  ) =>
    request<{ queued: boolean; message_id: number | null; conversation: AvitoConversation }>(
      `/conversations/${conversationId}/reply`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  setControl: (
    conversationId: number,
    payload: { mode: "ai" | "human" | "paused"; expected_version: number; permanent?: boolean; operator_name?: string },
  ) =>
    request<{ conversation: AvitoConversation }>(`/conversations/${conversationId}/control`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  markRead: (conversationId: number, throughMessageId: number) =>
    request<{ conversation: AvitoConversation }>(`/conversations/${conversationId}/read`, {
      method: "POST",
      body: JSON.stringify({ through_message_id: throughMessageId }),
    }),
};
