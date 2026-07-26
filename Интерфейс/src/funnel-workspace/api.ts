import type {
  Conversation,
  ConversationStatus,
  ConversationsPayload,
  MessagesPayload,
  WorkspaceMeta,
  WorkspaceSession,
} from "./types";

const API_ROOT = "/api/funnel-workspace";

type ApiErrorPayload = {
  error?: unknown;
  message?: unknown;
  detail?: unknown;
  code?: unknown;
};

export class FunnelWorkspaceApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "FunnelWorkspaceApiError";
    this.status = status;
    this.code = code;
  }
}

const asNonEmptyString = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

const extractApiMessage = (payload: ApiErrorPayload | null, status: number): string => {
  const nestedError =
    payload?.error && typeof payload.error === "object"
      ? (payload.error as { message?: unknown; code?: unknown })
      : null;
  return (
    asNonEmptyString(payload?.message) ||
    asNonEmptyString(nestedError?.message) ||
    asNonEmptyString(payload?.detail) ||
    asNonEmptyString(payload?.error) ||
    (status === 401
      ? "Сессия завершилась. Войдите снова."
      : status === 403
        ? "Недостаточно прав для этого действия."
        : status === 409
          ? "Диалог уже изменился. Данные обновлены — повторите действие."
          : status === 429
            ? "Слишком много попыток. Подождите немного."
            : status >= 500
              ? "Сервис временно недоступен."
              : `Ошибка запроса (${status}).`)
  );
};

const parseBody = async (response: Response): Promise<unknown> => {
  const raw = await response.text();
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    if (!response.ok) {
      throw new FunnelWorkspaceApiError(
        `Сервер вернул некорректный ответ (${response.status}).`,
        response.status,
      );
    }
    throw new FunnelWorkspaceApiError("Не удалось прочитать ответ сервера.", response.status);
  }
};

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { acceptUnauthenticatedSession?: boolean } = {},
): Promise<T> {
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
    throw new FunnelWorkspaceApiError(
      "Нет связи с сервером. Проверьте подключение и попробуйте снова.",
      0,
    );
  }

  const body = await parseBody(response);
  if (!response.ok) {
    if (
      options.acceptUnauthenticatedSession &&
      response.status === 401 &&
      body &&
      typeof body === "object" &&
      "authenticated" in body
    ) {
      return body as T;
    }
    const payload = body && typeof body === "object" ? (body as ApiErrorPayload) : null;
    const nestedCode =
      payload?.error && typeof payload.error === "object"
        ? (payload.error as { code?: unknown }).code
        : null;
    const code = asNonEmptyString(payload?.code) || asNonEmptyString(nestedCode);
    throw new FunnelWorkspaceApiError(extractApiMessage(payload, response.status), response.status, code);
  }
  return body as T;
}

const csrfHeaders = (csrfToken: string): HeadersInit => ({
  "X-CSRF-Token": csrfToken,
});

export interface WorkspaceExportParams {
  q?: string;
  status?: string;
  stage?: string;
  source?: string;
  authorType?: "client" | "agent" | "operator" | "system" | "";
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
}

const exportUrl = (params: WorkspaceExportParams = {}) => {
  const search = new URLSearchParams();
  if (params.q?.trim()) search.set("q", params.q.trim());
  if (params.status && params.status !== "all") search.set("status", params.status);
  if (params.stage && params.stage !== "all") search.set("stage", params.stage);
  if (params.source) search.set("source", params.source);
  if (params.authorType) search.set("author_type", params.authorType);
  if (params.dateFrom) search.set("date_from", params.dateFrom);
  if (params.dateTo) search.set("date_to", params.dateTo);
  if (params.limit) search.set("limit", String(params.limit));
  const query = search.size ? `?${search.toString()}` : "";
  return `${API_ROOT}/export.csv${query}`;
};

export const funnelWorkspaceApi = {
  getSession: () =>
    request<WorkspaceSession>("/session", {}, { acceptUnauthenticatedSession: true }),

  login: (password: string, operatorName: string) =>
    request<WorkspaceSession>("/session", {
      method: "POST",
      body: JSON.stringify({ password, operator_name: operatorName }),
    }),

  configurePassword: (
    adminPassword: string,
    newPassword: string,
    csrfToken: string,
  ) =>
    request<{ configured: boolean; can_configure: boolean }>("/configure-password", {
      method: "POST",
      headers: csrfHeaders(csrfToken),
      body: JSON.stringify({
        admin_password: adminPassword,
        new_password: newPassword,
        csrf_token: csrfToken,
      }),
    }),

  logout: (csrfToken: string) =>
    request<WorkspaceSession>("/session", {
      method: "DELETE",
      headers: csrfHeaders(csrfToken),
      body: JSON.stringify({ csrf_token: csrfToken }),
    }),

  getMeta: () => request<WorkspaceMeta>("/meta"),

  exportUrl,

  getConversations: (
    params: {
      q?: string;
      status?: string;
      stage?: string;
      urgency?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.status && params.status !== "all") search.set("status", params.status);
    if (params.stage && params.stage !== "all") search.set("stage", params.stage);
    if (params.urgency && params.urgency !== "all") search.set("urgency", params.urgency);
    search.set("limit", String(params.limit || 100));
    search.set("offset", String(params.offset || 0));
    return request<ConversationsPayload>(`/conversations?${search.toString()}`);
  },

  getConversation: (conversationId: Conversation["id"]) =>
    request<{ conversation: Conversation }>(
      `/conversations/${encodeURIComponent(String(conversationId))}`,
    ),

  getMessages: (
    conversationId: Conversation["id"],
    params: { afterId?: number; beforeId?: number; limit?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.afterId && params.afterId > 0) {
      search.set("after_id", String(params.afterId));
    }
    if (params.beforeId && params.beforeId > 0) {
      search.set("before_id", String(params.beforeId));
    }
    if (params.limit) search.set("limit", String(params.limit));
    const query = search.size ? `?${search.toString()}` : "";
    return request<MessagesPayload>(
      `/conversations/${encodeURIComponent(String(conversationId))}/messages${query}`,
    );
  },

  sendMessage: (
    conversationId: Conversation["id"],
    payload: {
      text: string;
      expected_version: number;
      csrf_token: string;
      idempotency_key: string;
    },
  ) =>
    request<Record<string, unknown>>(
      `/conversations/${encodeURIComponent(String(conversationId))}/messages`,
      {
        method: "POST",
        headers: csrfHeaders(payload.csrf_token),
        body: JSON.stringify(payload),
      },
    ),

  deleteConversation: (
    conversationId: Conversation["id"],
    csrfToken: string,
  ) =>
    request<{ deleted: boolean; messages: number; client: string }>(
      `/conversations/${encodeURIComponent(String(conversationId))}`,
      {
        method: "DELETE",
        headers: csrfHeaders(csrfToken),
        body: JSON.stringify({ csrf_token: csrfToken }),
      },
    ),

  setStage: (
    conversationId: Conversation["id"],
    payload: {
      stage: string;
      expected_version: number;
      csrf_token: string;
    },
  ) =>
    request<{ conversation: Conversation }>(
      `/conversations/${encodeURIComponent(String(conversationId))}/stage`,
      {
        method: "POST",
        headers: csrfHeaders(payload.csrf_token),
        body: JSON.stringify(payload),
      },
    ),

  setControl: (
    conversationId: Conversation["id"],
    payload: {
      mode: "human" | "ai";
      expected_version: number;
      csrf_token: string;
    },
  ) =>
    request<Record<string, unknown>>(
      `/conversations/${encodeURIComponent(String(conversationId))}/control`,
      {
        method: "POST",
        headers: csrfHeaders(payload.csrf_token),
        body: JSON.stringify(payload),
      },
    ),

  setStatus: (
    conversationId: Conversation["id"],
    payload: {
      status: ConversationStatus;
      expected_version: number;
      csrf_token: string;
    },
  ) =>
    request<Record<string, unknown>>(
      `/conversations/${encodeURIComponent(String(conversationId))}`,
      {
        method: "PATCH",
        headers: csrfHeaders(payload.csrf_token),
        body: JSON.stringify(payload),
      },
    ),

  markRead: (
    conversationId: Conversation["id"],
    throughMessageId: number,
    csrfToken: string,
  ) =>
    request<Record<string, unknown>>(
      `/conversations/${encodeURIComponent(String(conversationId))}/read`,
      {
        method: "POST",
        headers: csrfHeaders(csrfToken),
        body: JSON.stringify({
          through_message_id: throughMessageId,
          csrf_token: csrfToken,
        }),
      },
    ),
};
