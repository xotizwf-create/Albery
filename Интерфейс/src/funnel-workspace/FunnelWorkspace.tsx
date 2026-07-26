import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  Check,
  CheckCheck,
  ChevronDown,
  CircleUserRound,
  Clock3,
  Download,
  ExternalLink,
  FileText,
  Info,
  Loader2,
  LockKeyhole,
  LogOut,
  MessageCircleMore,
  PanelRight,
  RefreshCw,
  Search,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { cn } from "../lib/utils";
import { FunnelWorkspaceApiError, funnelWorkspaceApi } from "./api";
import type {
  Conversation,
  ConversationMessage,
  ConversationStatus,
  WorkspaceMeta,
  WorkspaceSession,
} from "./types";

type MobilePane = "list" | "chat";
type ExportAuthor = "" | "client" | "agent" | "operator" | "system";

const FALLBACK_STATUSES = [
  { value: "new", label: "Новая", color: "#7c3aed" },
  { value: "open", label: "В работе", color: "#2563eb" },
  { value: "waiting", label: "Нужен человек", color: "#dc2626" },
  { value: "closed", label: "Закрыта", color: "#64748b" },
  { value: "spam", label: "Спам", color: "#475569" },
  { value: "expired", label: "Истекло окно ответа", color: "#d97706" },
];

const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  FALLBACK_STATUSES.map((item) => [item.value, item.label]),
);

const STATUS_COLORS: Record<string, string> = Object.fromEntries(
  FALLBACK_STATUSES.map((item) => [item.value, item.color]),
);

const stringId = (id: Conversation["id"] | null | undefined) =>
  id === null || id === undefined ? "" : String(id);

const parseTimestamp = (value: string | null | undefined): number => {
  if (!value) return 0;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
};

const formatClock = (value: string | null | undefined) => {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
};

const formatConversationTime = (value: string | null | undefined) => {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return "";
  const date = new Date(timestamp);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const dayDifference = Math.round((startToday - startDate) / 86_400_000);
  if (dayDifference === 0) return formatClock(value);
  if (dayDifference === 1) return "вчера";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
  }).format(date);
};

const formatMessageDay = (value: string) => {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return "";
  const date = new Date(timestamp);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const dayDifference = Math.round((startToday - startDate) / 86_400_000);
  if (dayDifference === 0) return "Сегодня";
  if (dayDifference === 1) return "Вчера";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  }).format(date);
};

const messageDayKey = (value: string) => {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return value;
  const date = new Date(timestamp);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
};

const formatLease = (resumeAt: string | null, now: number) => {
  const remaining = Math.max(0, Math.ceil((parseTimestamp(resumeAt) - now) / 1000));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return {
    remaining,
    text: `${minutes}:${String(seconds).padStart(2, "0")}`,
  };
};

const initials = (name: string) => {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "TG";
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
};

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Неизвестная ошибка. Попробуйте снова.";

const isUnauthorized = (error: unknown) =>
  error instanceof FunnelWorkspaceApiError && error.status === 401;

function LoadingScreen() {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-[#F4F6FA] text-slate-600">
      <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <Loader2 className="h-5 w-5 animate-spin text-[#635BFF]" />
        <span className="text-sm font-semibold">Открываем рабочее пространство…</span>
      </div>
    </div>
  );
}

function SessionError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-[#F4F6FA] p-5">
      <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-xl shadow-slate-200/50">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-red-50 text-red-600">
          <WifiOff className="h-6 w-6" />
        </span>
        <h1 className="mt-5 text-xl font-extrabold text-slate-950">Не удалось открыть страницу</h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-sm font-bold text-white transition hover:bg-slate-800"
        >
          <RefreshCw className="h-4 w-4" />
          Повторить
        </button>
      </div>
    </div>
  );
}

function WorkspaceDisabled({
  session,
  onLogout,
}: {
  session: WorkspaceSession;
  onLogout: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const leave = async () => {
    if (session.admin_session) {
      window.location.assign("/");
      return;
    }
    if (!session.csrf_token || busy) return;
    setBusy(true);
    setError("");
    try {
      await funnelWorkspaceApi.logout(session.csrf_token);
      onLogout();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-[#F4F6FA] p-5">
      <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-xl shadow-slate-200/50">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-600">
          <Clock3 className="h-6 w-6" />
        </span>
        <h1 className="mt-5 text-xl font-extrabold text-slate-950">
          Рабочее пространство пока выключено
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          Пароль настроен, но приём Telegram-обращений ещё не включён. Это
          безопасный режим перед сквозным тестом.
        </p>
        {error && (
          <div className="mt-4 rounded-xl border border-red-100 bg-red-50 px-3.5 py-3 text-sm font-medium text-red-700">
            {error}
          </div>
        )}
        <button
          type="button"
          onClick={() => void leave()}
          disabled={busy}
          className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-sm font-bold text-white transition hover:bg-slate-800 disabled:opacity-60"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
          {session.admin_session ? "В кабинет" : "Выйти"}
        </button>
      </div>
    </div>
  );
}

function WorkspaceLogin({
  configured,
  onAuthenticated,
}: {
  configured: boolean;
  onAuthenticated: (session: WorkspaceSession) => void;
}) {
  const [operatorName, setOperatorName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!operatorName.trim() || !password || submitting || !configured) return;
    setSubmitting(true);
    setError("");
    try {
      const nextSession = await funnelWorkspaceApi.login(password, operatorName.trim());
      if (!nextSession.authenticated) {
        setError("Неверный пароль.");
        return;
      }
      onAuthenticated(nextSession);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-[#F4F6FA] p-5">
      <div className="pointer-events-none absolute -left-40 -top-40 h-[480px] w-[480px] rounded-full bg-violet-200/45 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-48 -right-32 h-[520px] w-[520px] rounded-full bg-blue-200/40 blur-3xl" />

      <div className="relative w-full max-w-[430px]">
        <div className="mb-7 flex items-center justify-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-[#7167FF] to-[#4E43E8] text-lg font-black text-white shadow-lg shadow-violet-300/50">
            A
          </span>
          <div>
            <div className="text-xl font-black tracking-tight text-slate-950">Alberi</div>
            <div className="text-xs font-semibold text-slate-400">Работа с обращениями</div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="rounded-[28px] border border-white/80 bg-white/95 p-7 shadow-2xl shadow-slate-300/35 backdrop-blur sm:p-9"
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-[#5B50EA]">
            <LockKeyhole className="h-6 w-6" />
          </span>
          <h1 className="mt-5 text-2xl font-black tracking-tight text-slate-950">
            Вход в рабочее пространство
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Отдельный защищённый доступ к заявкам и переписке с клиентами.
          </p>

          {!configured ? (
            <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm leading-6 text-amber-900">
              <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
                <div>
                  <div className="font-bold">Пароль ещё не настроен</div>
                  <div className="mt-1 text-amber-800">
                    Администратору нужно добавить хэш пароля в конфигурацию сервера.
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <>
              <label className="mt-6 block">
                <span className="mb-2 block text-xs font-bold uppercase tracking-[0.12em] text-slate-400">
                  Ваше имя
                </span>
                <input
                  type="text"
                  value={operatorName}
                  onChange={(event) => setOperatorName(event.target.value)}
                  autoComplete="name"
                  autoFocus
                  maxLength={80}
                  placeholder="Например, Александр"
                  className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-[15px] font-medium text-slate-900 transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white"
                />
              </label>

              <label className="mt-4 block">
                <span className="mb-2 block text-xs font-bold uppercase tracking-[0.12em] text-slate-400">
                  Пароль
                </span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  placeholder="Введите пароль"
                  className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-[15px] font-medium text-slate-900 transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white"
                />
              </label>

              {error && (
                <div
                  role="alert"
                  className="mt-4 flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-3.5 py-3 text-sm font-medium text-red-700"
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={submitting || !operatorName.trim() || !password}
                className="mt-5 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#5B50EA] px-5 text-sm font-bold text-white shadow-lg shadow-violet-200 transition hover:bg-[#4F45DB] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                {submitting ? "Проверяем…" : "Войти"}
              </button>
            </>
          )}
        </form>

        <div className="mt-5 flex items-center justify-center gap-2 text-xs font-medium text-slate-400">
          <ShieldCheck className="h-4 w-4" />
          Пароль проверяется сервером, доступ хранится в защищённой сессии
        </div>
      </div>
    </div>
  );
}

function WorkspacePasswordSetup({
  session,
  onConfigured,
}: {
  session: WorkspaceSession;
  onConfigured: () => void;
}) {
  const [adminPassword, setAdminPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    if (newPassword.length < 12) {
      setError("Новый пароль должен содержать не менее 12 символов.");
      return;
    }
    if (newPassword !== confirmation) {
      setError("Новые пароли не совпадают.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      // Вкладку с этой формой держат открытой, а вход в кабинет мог произойти позже и
      // пересоздать сессию. Токен из старого состояния к этому моменту уже не подходит,
      // поэтому берём свежий прямо перед отправкой.
      const fresh = await funnelWorkspaceApi.getSession();
      const csrfToken = fresh.csrf_token ?? session.csrf_token;
      if (!csrfToken) {
        setError("Сессия настройки устарела. Обновите страницу.");
        return;
      }
      await funnelWorkspaceApi.configurePassword(
        adminPassword,
        newPassword,
        csrfToken,
      );
      setAdminPassword("");
      setNewPassword("");
      setConfirmation("");
      onConfigured();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-[#F4F6FA] p-5">
      <div className="pointer-events-none absolute -left-40 -top-40 h-[480px] w-[480px] rounded-full bg-violet-200/45 blur-3xl" />
      <div className="relative w-full max-w-[470px] rounded-[28px] border border-white/80 bg-white/95 p-7 shadow-2xl shadow-slate-300/35 sm:p-9">
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-[#5B50EA]">
          <ShieldCheck className="h-6 w-6" />
        </span>
        <h1 className="mt-5 text-2xl font-black tracking-tight text-slate-950">
          Задайте отдельный пароль
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          После настройки сотрудники смогут открывать эту страницу без входа в основной
          кабинет. На сервере сохранится только scrypt-хэш.
        </p>

        <form onSubmit={submit}>
          <label className="mt-6 block">
            <span className="mb-2 block text-xs font-bold uppercase tracking-[0.12em] text-slate-400">
              Текущий пароль администратора
            </span>
            <input
              type="password"
              value={adminPassword}
              onChange={(event) => setAdminPassword(event.target.value)}
              autoComplete="current-password"
              autoFocus
              className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-[15px] font-medium text-slate-900 focus:border-violet-300 focus:bg-white"
            />
          </label>
          <label className="mt-4 block">
            <span className="mb-2 block text-xs font-bold uppercase tracking-[0.12em] text-slate-400">
              Новый пароль рабочего пространства
            </span>
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-[15px] font-medium text-slate-900 focus:border-violet-300 focus:bg-white"
            />
          </label>
          <label className="mt-4 block">
            <span className="mb-2 block text-xs font-bold uppercase tracking-[0.12em] text-slate-400">
              Повторите новый пароль
            </span>
            <input
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              className="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-[15px] font-medium text-slate-900 focus:border-violet-300 focus:bg-white"
            />
          </label>

          {error && (
            <div
              role="alert"
              className="mt-4 flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-3.5 py-3 text-sm font-medium text-red-700"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={
              submitting ||
              !adminPassword ||
              newPassword.length < 12 ||
              confirmation.length < 12
            }
            className="mt-5 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#5B50EA] px-5 text-sm font-bold text-white shadow-lg shadow-violet-200 transition hover:bg-[#4F45DB] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <LockKeyhole className="h-4 w-4" />
            )}
            {submitting ? "Сохраняем…" : "Установить пароль"}
          </button>
        </form>
      </div>
    </div>
  );
}

function StatusPill({
  status,
  label,
  color,
  compact = false,
}: {
  status: string;
  label?: string;
  color?: string;
  compact?: boolean;
}) {
  const resolvedColor = color || STATUS_COLORS[status] || "#64748b";
  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center gap-1.5 rounded-full border bg-white font-bold",
        compact ? "px-2 py-1 text-[10px]" : "px-2.5 py-1.5 text-[11px]",
      )}
      style={{ borderColor: `${resolvedColor}2b`, color: resolvedColor }}
    >
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: resolvedColor }}
      />
      <span className="truncate">{label || STATUS_LABELS[status] || status}</span>
    </span>
  );
}

function ControlBadge({
  conversation,
  now,
  compact = false,
}: {
  conversation: Conversation;
  now: number;
  compact?: boolean;
}) {
  if (conversation.can_reply === false) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full bg-slate-100 font-bold text-slate-600",
          compact ? "px-2 py-1 text-[10px]" : "px-2.5 py-1.5 text-[11px]",
        )}
      >
        <Clock3 className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
        Ответ закрыт
      </span>
    );
  }

  if (conversation.control_mode === "ai") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full bg-violet-50 font-bold text-violet-700",
          compact ? "px-2 py-1 text-[10px]" : "px-2.5 py-1.5 text-[11px]",
        )}
      >
        <Sparkles className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
        ИИ отвечает
      </span>
    );
  }

  if (conversation.control_mode === "paused") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full bg-amber-50 font-bold text-amber-700",
          compact ? "px-2 py-1 text-[10px]" : "px-2.5 py-1.5 text-[11px]",
        )}
      >
        <Clock3 className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
        Ответы приостановлены
      </span>
    );
  }

  const lease = formatLease(conversation.resume_at, now);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-emerald-50 font-bold text-emerald-700",
        compact ? "px-2 py-1 text-[10px]" : "px-2.5 py-1.5 text-[11px]",
      )}
    >
      <UserRound className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
      {lease.remaining > 0 ? `Человек · ${lease.text}` : "Возврат ИИ…"}
    </span>
  );
}

function ConversationList({
  conversations,
  selectedId,
  loading,
  loadingMore,
  total,
  query,
  status,
  statusOptions,
  now,
  onQueryChange,
  onStatusChange,
  onSelect,
  onRefresh,
  onLoadMore,
}: {
  conversations: Conversation[];
  selectedId: Conversation["id"] | null;
  loading: boolean;
  loadingMore: boolean;
  total: number;
  query: string;
  status: string;
  statusOptions: Array<{ value: string; label: string; color?: string }>;
  now: number;
  onQueryChange: (value: string) => void;
  onStatusChange: (value: string) => void;
  onSelect: (conversation: Conversation) => void;
  onRefresh: () => void;
  onLoadMore: () => void;
}) {
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDateFrom, setExportDateFrom] = useState("");
  const [exportDateTo, setExportDateTo] = useState("");
  const [exportAuthor, setExportAuthor] = useState<ExportAuthor>("");
  const unreadTotal = conversations.reduce(
    (sum, conversation) => sum + Math.max(0, conversation.unread_count || 0),
    0,
  );
  const invalidExportRange = Boolean(
    exportDateFrom && exportDateTo && exportDateFrom > exportDateTo,
  );
  const hasExportFilters = Boolean(exportDateFrom || exportDateTo || exportAuthor);
  const exportHref = useMemo(
    () =>
      funnelWorkspaceApi.exportUrl({
        q: query,
        status,
        authorType: exportAuthor,
        dateFrom: exportDateFrom,
        dateTo: exportDateTo,
        limit: 20_000,
      }),
    [exportAuthor, exportDateFrom, exportDateTo, query, status],
  );

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-4 pb-4 pt-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-black tracking-tight text-slate-950">Обращения</h2>
            <p className="mt-0.5 text-xs font-semibold text-slate-400">
              {conversations.length} из {total || conversations.length}
              {unreadTotal > 0 ? ` · ${unreadTotal} непрочитано` : ""}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setExportOpen((current) => !current)}
              aria-label="Настроить экспорт журнала"
              aria-expanded={exportOpen}
              title="Экспорт журнала в CSV"
              className={cn(
                "inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border px-2.5 text-[11px] font-bold transition",
                exportOpen
                  ? "border-violet-200 bg-violet-50 text-violet-700"
                  : "border-slate-200 text-slate-500 hover:bg-slate-50",
              )}
            >
              <Download className="h-3.5 w-3.5" />
              CSV
            </button>
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              aria-label="Обновить обращения"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-500 transition hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </button>
          </div>
        </div>

        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Имя, @username или сообщение"
            className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 pl-10 pr-9 text-sm font-medium text-slate-800 transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white"
          />
          {query && (
            <button
              type="button"
              onClick={() => onQueryChange("")}
              aria-label="Очистить поиск"
              className="absolute right-2.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-200 hover:text-slate-600"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <div className="relative mt-2">
          <select
            value={status}
            onChange={(event) => onStatusChange(event.target.value)}
            className="h-10 w-full appearance-none rounded-xl border border-slate-200 bg-white px-3 pr-9 text-xs font-bold text-slate-600"
          >
            <option value="all">Все статусы</option>
            {statusOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        </div>

        {exportOpen && (
          <div className="mt-3 rounded-2xl border border-violet-100 bg-violet-50/55 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-[11px] font-black text-slate-800">CSV-журнал сообщений</div>
                <div className="mt-0.5 text-[10px] font-medium text-slate-500">
                  Поиск и статус выше тоже учитываются
                </div>
              </div>
              {hasExportFilters && (
                <button
                  type="button"
                  onClick={() => {
                    setExportDateFrom("");
                    setExportDateTo("");
                    setExportAuthor("");
                  }}
                  className="shrink-0 text-[10px] font-bold text-violet-600 hover:text-violet-800"
                >
                  Сбросить
                </button>
              )}
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="block min-w-0">
                <span className="mb-1 block text-[9px] font-black uppercase tracking-[0.08em] text-slate-400">
                  Дата от
                </span>
                <input
                  type="date"
                  value={exportDateFrom}
                  max={exportDateTo || undefined}
                  onChange={(event) => setExportDateFrom(event.target.value)}
                  className="h-9 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-2 text-[11px] font-bold text-slate-700"
                />
              </label>
              <label className="block min-w-0">
                <span className="mb-1 block text-[9px] font-black uppercase tracking-[0.08em] text-slate-400">
                  Дата до
                </span>
                <input
                  type="date"
                  value={exportDateTo}
                  min={exportDateFrom || undefined}
                  onChange={(event) => setExportDateTo(event.target.value)}
                  className="h-9 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-2 text-[11px] font-bold text-slate-700"
                />
              </label>
            </div>

            <label className="mt-2 block">
              <span className="mb-1 block text-[9px] font-black uppercase tracking-[0.08em] text-slate-400">
                Автор сообщения
              </span>
              <div className="relative">
                <select
                  value={exportAuthor}
                  onChange={(event) => setExportAuthor(event.target.value as ExportAuthor)}
                  className="h-9 w-full appearance-none rounded-xl border border-slate-200 bg-white px-2.5 pr-8 text-[11px] font-bold text-slate-700"
                >
                  <option value="">Все типы</option>
                  <option value="client">Клиент</option>
                  <option value="agent">ИИ-агент</option>
                  <option value="operator">Менеджер</option>
                  <option value="system">Система</option>
                </select>
                <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
              </div>
            </label>

            {invalidExportRange && (
              <div className="mt-2 text-[10px] font-semibold text-red-600">
                Начальная дата должна быть не позже конечной.
              </div>
            )}

            <a
              href={invalidExportRange ? undefined : exportHref}
              download="funnel-workspace-messages.csv"
              aria-disabled={invalidExportRange}
              tabIndex={invalidExportRange ? -1 : 0}
              onClick={(event) => {
                if (invalidExportRange) event.preventDefault();
              }}
              className={cn(
                "mt-3 inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl text-[11px] font-black transition",
                invalidExportRange
                  ? "cursor-not-allowed bg-slate-200 text-slate-400"
                  : "bg-[#5B50EA] text-white hover:bg-[#4F45DB]",
              )}
            >
              <Download className="h-3.5 w-3.5" />
              Скачать CSV · до 20 000 сообщений
            </a>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && conversations.length === 0 ? (
          <div className="space-y-2 p-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="animate-pulse rounded-2xl border border-slate-100 p-3.5">
                <div className="flex gap-3">
                  <div className="h-10 w-10 rounded-full bg-slate-100" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="h-3.5 w-2/3 rounded bg-slate-100" />
                    <div className="h-3 w-full rounded bg-slate-100" />
                    <div className="h-3 w-1/2 rounded bg-slate-100" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <div className="flex h-full min-h-64 flex-col items-center justify-center px-8 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-slate-400">
              <MessageCircleMore className="h-6 w-6" />
            </span>
            <div className="mt-4 text-sm font-bold text-slate-700">Обращений не найдено</div>
            <div className="mt-1 text-xs leading-5 text-slate-400">
              Измените поиск или фильтр. Новые сообщения из Telegram появятся здесь автоматически.
            </div>
          </div>
        ) : (
          <div className="p-2">
            {conversations.map((conversation) => {
              const active = stringId(conversation.id) === stringId(selectedId);
              return (
                <button
                  type="button"
                  key={conversation.id}
                  onClick={() => onSelect(conversation)}
                  className={cn(
                    "mb-1 w-full rounded-2xl border px-3 py-3 text-left transition",
                    active
                      ? "border-violet-200 bg-violet-50/80 shadow-sm"
                      : "border-transparent hover:border-slate-100 hover:bg-slate-50",
                  )}
                >
                  <div className="flex items-start gap-3">
                    <span
                      className={cn(
                        "flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-black",
                        active
                          ? "bg-[#5B50EA] text-white"
                          : "bg-gradient-to-br from-sky-100 to-violet-100 text-violet-700",
                      )}
                    >
                      {initials(conversation.display_name || conversation.username || "Telegram")}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-start justify-between gap-2">
                        <span
                          className={cn(
                            "truncate text-sm",
                            conversation.unread_count > 0
                              ? "font-black text-slate-950"
                              : "font-bold text-slate-800",
                          )}
                        >
                          {conversation.display_name || conversation.username || "Клиент Telegram"}
                        </span>
                        <span className="shrink-0 text-[10px] font-semibold text-slate-400">
                          {formatConversationTime(conversation.last_message_at)}
                        </span>
                      </span>
                      <span className="mt-1 flex items-start gap-2">
                        <span
                          className={cn(
                            "line-clamp-2 min-h-[34px] flex-1 text-xs leading-[17px]",
                            conversation.unread_count > 0
                              ? "font-semibold text-slate-700"
                              : "font-medium text-slate-400",
                          )}
                        >
                          {conversation.last_message || "Диалог пока пуст"}
                        </span>
                        {conversation.unread_count > 0 && (
                          <span className="mt-0.5 flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-[#5B50EA] px-1.5 text-[10px] font-black text-white">
                            {conversation.unread_count > 99 ? "99+" : conversation.unread_count}
                          </span>
                        )}
                      </span>
                      <span className="mt-2 flex min-w-0 flex-wrap items-center gap-1.5">
                        <StatusPill status={conversation.status} compact />
                        <ControlBadge conversation={conversation} now={now} compact />
                      </span>
                    </span>
                  </div>
                </button>
              );
            })}
            {conversations.length < total && (
              <button
                type="button"
                onClick={onLoadMore}
                disabled={loadingMore}
                className="mb-2 mt-2 inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-xs font-bold text-slate-600 transition hover:bg-slate-50 disabled:opacity-60"
              >
                {loadingMore && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Показать ещё
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function DeliveryStatus({ message }: { message: ConversationMessage }) {
  const status = (message.delivery_status || "").toLowerCase();
  if (status === "unknown") {
    return (
      <span
        className="inline-flex items-center gap-1 font-semibold text-amber-600"
        title="Telegram не подтвердил доставку"
      >
        <AlertCircle className="h-3 w-3" />
        Неизвестно
      </span>
    );
  }
  if (message.error || status === "failed" || status === "error") {
    return (
      <span className="inline-flex items-center gap-1 font-semibold text-red-500" title={message.error || ""}>
        <AlertCircle className="h-3 w-3" />
        Не отправлено
      </span>
    );
  }
  if (
    message.optimistic ||
    ["sending", "queued", "pending", "leased"].includes(status)
  ) {
    return (
      <span className="inline-flex items-center gap-1 text-slate-400">
        <Clock3 className="h-3 w-3" />
        Отправляется
      </span>
    );
  }
  if (status === "read") {
    return (
      <span className="inline-flex items-center text-blue-500" title="Прочитано">
        <CheckCheck className="h-3.5 w-3.5" />
      </span>
    );
  }
  if (status === "delivered") {
    return (
      <span className="inline-flex items-center text-slate-400" title="Доставлено">
        <CheckCheck className="h-3.5 w-3.5" />
      </span>
    );
  }
  return (
    <span className="inline-flex items-center text-slate-400" title="Отправлено">
      <Check className="h-3.5 w-3.5" />
    </span>
  );
}

const formatAttachmentSize = (bytes: number | null | undefined) => {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} МБ`;
};

function MessageAttachment({
  attachment,
}: {
  attachment: NonNullable<ConversationMessage["attachment"]>;
}) {
  const size = formatAttachmentSize(attachment.file_size);
  const details = [attachment.file_name, size].filter(Boolean).join(" · ");

  if (attachment.media_type === "photo") {
    return (
      <div className="mb-2">
        <img
          src={attachment.url}
          alt={attachment.file_name || "Фото из Telegram"}
          loading="lazy"
          className="max-h-80 w-auto max-w-full rounded-xl bg-slate-100 object-contain"
        />
        <a
          href={attachment.download_url}
          download={attachment.file_name}
          className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-bold text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-700"
        >
          <Download className="h-3 w-3" />
          {details || "Скачать фото"}
        </a>
      </div>
    );
  }

  if (attachment.media_type === "voice" || attachment.media_type === "audio") {
    return (
      <div className="mb-2 min-w-[240px] max-w-full">
        <audio
          controls
          preload="metadata"
          src={attachment.url}
          className="h-10 w-full max-w-[320px]"
        >
          Ваш браузер не поддерживает воспроизведение аудио.
        </audio>
        <a
          href={attachment.download_url}
          download={attachment.file_name}
          className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-bold text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-700"
        >
          <Download className="h-3 w-3" />
          {details || "Скачать аудио"}
        </a>
      </div>
    );
  }

  return (
    <a
      href={attachment.download_url}
      download={attachment.file_name}
      className="mb-2 flex min-w-[220px] max-w-full items-center gap-2.5 rounded-xl border border-slate-200 bg-white/70 px-3 py-2.5 text-left transition hover:bg-white"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
        <FileText className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-bold text-slate-700">
          {attachment.file_name || "Вложение Telegram"}
        </span>
        <span className="mt-0.5 block text-[10px] font-medium text-slate-400">
          {[attachment.media_type, size].filter(Boolean).join(" · ")}
        </span>
      </span>
      <Download className="h-4 w-4 shrink-0 text-slate-400" />
    </a>
  );
}

function MessageBubble({
  message,
  onRetry,
}: {
  message: ConversationMessage;
  onRetry: (message: ConversationMessage) => void;
}) {
  if (message.author_type === "system") {
    return (
      <div className="my-3 flex justify-center px-4">
        <div className="max-w-[85%] rounded-full bg-slate-200/70 px-3 py-1.5 text-center text-[11px] font-semibold text-slate-500">
          {message.text}
          {message.created_at && <span className="ml-1.5 opacity-70">{formatClock(message.created_at)}</span>}
        </div>
      </div>
    );
  }

  const incoming = message.direction === "incoming" || message.author_type === "client";
  const isAi = message.author_type === "ai";
  const deliveryStatus = (message.delivery_status || "").toLowerCase();
  const uncertain = deliveryStatus === "unknown";
  const failed = !uncertain && (
    Boolean(message.error) ||
    ["failed", "error"].includes(deliveryStatus)
  );

  return (
    <div className={cn("flex px-4 py-1.5 sm:px-6", incoming ? "justify-start" : "justify-end")}>
      <div className={cn("flex max-w-[88%] items-end gap-2 sm:max-w-[72%]", !incoming && "flex-row-reverse")}>
        <span
          className={cn(
            "mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
            incoming
              ? "bg-white text-slate-500 shadow-sm ring-1 ring-slate-200"
              : isAi
                ? "bg-violet-100 text-violet-700"
                : "bg-emerald-100 text-emerald-700",
          )}
        >
          {incoming ? (
            <CircleUserRound className="h-4 w-4" />
          ) : isAi ? (
            <Bot className="h-4 w-4" />
          ) : (
            <UserRound className="h-4 w-4" />
          )}
        </span>
        <div
          className={cn(
            "min-w-0 rounded-2xl px-3.5 py-2.5 shadow-sm",
            incoming
              ? "rounded-bl-md border border-slate-200 bg-white text-slate-800"
              : isAi
                ? "rounded-br-md border border-violet-100 bg-[#F0EEFF] text-slate-800"
                : failed
                  ? "rounded-br-md border border-red-200 bg-red-50 text-slate-800"
                  : "rounded-br-md border border-emerald-100 bg-[#EAF8F2] text-slate-800",
          )}
        >
          <div className="mb-1 flex items-center gap-1.5 text-[10px] font-black uppercase tracking-[0.08em]">
            <span
              className={cn(
                incoming ? "text-slate-400" : isAi ? "text-violet-600" : "text-emerald-600",
              )}
            >
              {incoming
                ? message.author_name || "Клиент"
                : isAi
                  ? "ИИ-агент"
                  : message.author_name || "Менеджер"}
            </span>
          </div>
          {message.attachment && <MessageAttachment attachment={message.attachment} />}
          <div className="whitespace-pre-wrap break-words text-[14px] leading-[21px]">{message.text}</div>
          <div className="mt-1.5 flex items-center justify-end gap-1.5 text-[10px] font-medium text-slate-400">
            <span>{formatClock(message.created_at)}</span>
            {!incoming && <DeliveryStatus message={message} />}
          </div>
          {failed && (
            <button
              type="button"
              onClick={() => onRetry(message)}
              className="mt-2 text-[11px] font-bold text-red-600 underline decoration-red-300 underline-offset-2"
            >
              Повторить отправку
            </button>
          )}
          {uncertain && (
            <div className="mt-2 text-[11px] font-semibold leading-4 text-amber-700">
              Доставка не подтверждена. Проверьте чат в Telegram перед повторной отправкой.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ChatPanel({
  conversation,
  messages,
  loading,
  loadingOlder,
  hasOlderMessages,
  sending,
  controlBusy,
  now,
  onBack,
  onShowDetails,
  onLoadOlder,
  onSend,
  onRetry,
  onControl,
}: {
  conversation: Conversation | null;
  messages: ConversationMessage[];
  loading: boolean;
  loadingOlder: boolean;
  hasOlderMessages: boolean;
  sending: boolean;
  controlBusy: boolean;
  now: number;
  onBack: () => void;
  onShowDetails: () => void;
  onLoadOlder: () => void;
  onSend: (text: string) => void;
  onRetry: (message: ConversationMessage) => void;
  onControl: (mode: "human" | "ai") => void;
}) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const selectedKey = stringId(conversation?.id);
  const lease = conversation ? formatLease(conversation.resume_at, now) : null;
  const canReply = conversation?.can_reply !== false;
  const controlTarget =
    conversation?.control_mode === "ai"
      ? "human"
      : conversation?.control_mode === "paused"
        ? "human"
        : conversation?.ai_available
          ? "ai"
          : null;

  useEffect(() => {
    setDraft("");
    stickToBottomRef.current = true;
  }, [selectedKey]);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages.length, selectedKey]);

  const submitDraft = () => {
    const text = draft.trim();
    if (!text || sending || conversation?.control_mode !== "human") return;
    setDraft("");
    stickToBottomRef.current = true;
    onSend(text);
  };

  const onDraftKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitDraft();
    }
  };

  if (!conversation) {
    return (
      <main className="flex min-h-0 flex-1 flex-col items-center justify-center bg-[#F6F8FB] px-8 text-center">
        <span className="flex h-16 w-16 items-center justify-center rounded-3xl bg-white text-violet-500 shadow-sm ring-1 ring-slate-200">
          <MessageCircleMore className="h-8 w-8" />
        </span>
        <h2 className="mt-5 text-lg font-black text-slate-800">Выберите обращение</h2>
        <p className="mt-2 max-w-sm text-sm leading-6 text-slate-400">
          Здесь появится полная переписка. Можно наблюдать за ИИ или забрать диалог себе.
        </p>
      </main>
    );
  }

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-[#F6F8FB]">
      <header className="flex min-h-[72px] shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            aria-label="К списку обращений"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 md:hidden"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-100 to-violet-100 text-xs font-black text-violet-700">
            {initials(conversation.display_name || conversation.username || "Telegram")}
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-black text-slate-950 sm:text-[15px]">
              {conversation.display_name || conversation.username || "Клиент Telegram"}
            </h2>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-sky-500 text-[8px] font-black text-white">
                TG
              </span>
              <span className="truncate">
                {conversation.username ? `@${conversation.username.replace(/^@/, "")}` : "Telegram"}
              </span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <div className="hidden lg:block">
            <ControlBadge conversation={conversation} now={now} />
          </div>
          <button
            type="button"
            onClick={() => controlTarget && onControl(controlTarget)}
            disabled={controlBusy || !canReply || !controlTarget}
            className={cn(
              "hidden h-10 items-center justify-center gap-2 rounded-xl px-3 text-xs font-bold transition sm:inline-flex",
              conversation.control_mode === "ai"
                ? "bg-slate-950 text-white hover:bg-slate-800"
                : "border border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100",
            )}
          >
            {controlBusy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : conversation.control_mode === "ai" ? (
              <UserRound className="h-4 w-4" />
            ) : (
              <Bot className="h-4 w-4" />
            )}
            {conversation.control_mode === "ai"
              ? "Забрать"
              : conversation.control_mode === "paused"
                ? "Взять диалог"
                : conversation.ai_available
                  ? "Вернуть ИИ"
                  : "ИИ недоступен"}
          </button>
          <button
            type="button"
            onClick={onShowDetails}
            aria-label="Показать детали"
            className="flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-500 hover:bg-slate-50 xl:hidden"
          >
            <PanelRight className="h-4 w-4" />
          </button>
        </div>
      </header>

      {conversation.control_mode === "human" && lease && (
        <div className="flex shrink-0 items-center justify-center gap-2 border-b border-emerald-100 bg-emerald-50 px-4 py-2 text-center text-[11px] font-semibold text-emerald-800">
          <Clock3 className="h-3.5 w-3.5" />
          {lease.remaining > 0
            ? `ИИ молчит. Автовозврат через ${lease.text}; каждое ваше сообщение продлевает время.`
            : "Время ручного режима истекло — обновляем состояние диалога."}
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const node = event.currentTarget;
          stickToBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
        }}
        className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top_left,rgba(124,58,237,0.035),transparent_36%),radial-gradient(circle_at_bottom_right,rgba(14,165,233,0.045),transparent_38%)] py-4"
      >
        {loading ? (
          <div className="flex h-full min-h-64 items-center justify-center">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin text-violet-500" />
              Загружаем переписку…
            </div>
          </div>
        ) : messages.length === 0 ? (
          <div className="flex h-full min-h-64 items-center justify-center px-6 text-center">
            <div>
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-slate-400 shadow-sm ring-1 ring-slate-200">
                <MessageCircleMore className="h-6 w-6" />
              </span>
              <div className="mt-3 text-sm font-bold text-slate-600">Сообщений пока нет</div>
            </div>
          </div>
        ) : (
          <>
            {hasOlderMessages && (
              <div className="mb-2 flex justify-center">
                <button
                  type="button"
                  onClick={onLoadOlder}
                  disabled={loadingOlder}
                  className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 text-[11px] font-bold text-slate-500 shadow-sm backdrop-blur hover:bg-white disabled:opacity-60"
                >
                  {loadingOlder && <Loader2 className="h-3 w-3 animate-spin" />}
                  Загрузить предыдущие сообщения
                </button>
              </div>
            )}
            {messages.map((message, index) => {
              const previous = messages[index - 1];
              const showDay =
                !previous || messageDayKey(previous.created_at) !== messageDayKey(message.created_at);
              return (
                <div key={`${message.id}-${message.optimistic ? "local" : "server"}`}>
                  {showDay && (
                    <div className="my-3 flex items-center justify-center">
                      <span className="rounded-full border border-slate-200 bg-white/90 px-3 py-1 text-[10px] font-bold text-slate-400 shadow-sm">
                        {formatMessageDay(message.created_at)}
                      </span>
                    </div>
                  )}
                  <MessageBubble message={message} onRetry={onRetry} />
                </div>
              );
            })}
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-slate-200 bg-white px-3 py-3 sm:px-5">
        {!canReply ? (
          <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <div className="text-xs font-black">Ответ сейчас недоступен</div>
              <div className="mt-0.5 text-[11px] font-medium leading-4">
                Окно ответа Telegram закрыто либо заявка завершена. Дождитесь нового сообщения клиента.
              </div>
            </div>
          </div>
        ) : conversation.control_mode === "ai" ? (
          <div className="flex items-center justify-between gap-3 rounded-2xl border border-violet-100 bg-violet-50/80 px-3 py-3 sm:px-4">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-violet-600 shadow-sm">
                <Bot className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <div className="text-xs font-black text-violet-900">Сейчас отвечает ИИ-агент</div>
                <div className="mt-0.5 hidden truncate text-[11px] font-medium text-violet-600 sm:block">
                  Чтобы написать клиенту, заберите диалог
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onControl("human")}
              disabled={controlBusy}
              className="inline-flex h-9 shrink-0 items-center gap-2 rounded-xl bg-violet-600 px-3 text-xs font-bold text-white transition hover:bg-violet-700 disabled:opacity-60"
            >
              {controlBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UserRound className="h-3.5 w-3.5" />}
              Забрать
            </button>
          </div>
        ) : conversation.control_mode === "paused" ? (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-3 sm:px-4">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-amber-600 shadow-sm">
                <Clock3 className="h-4 w-4" />
              </span>
              <div>
                <div className="text-xs font-black text-amber-900">Автоответы приостановлены</div>
                <div className="mt-0.5 text-[11px] font-medium leading-4 text-amber-700">
                  Возьмите диалог себе или подключите ИИ, если он разрешён для этого теста.
                </div>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => onControl("human")}
                disabled={controlBusy}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-xl bg-slate-950 px-3 text-xs font-bold text-white disabled:opacity-60"
              >
                <UserRound className="h-3.5 w-3.5" />
                Взять себе
              </button>
              {conversation.ai_available && (
                <button
                  type="button"
                  onClick={() => onControl("ai")}
                  disabled={controlBusy}
                  className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-xl bg-violet-600 px-3 text-xs font-bold text-white disabled:opacity-60"
                >
                  <Bot className="h-3.5 w-3.5" />
                  Подключить ИИ
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-slate-50 p-2 transition focus-within:border-violet-300 focus-within:bg-white">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value.slice(0, 4096))}
                onKeyDown={onDraftKeyDown}
                rows={1}
                placeholder="Напишите клиенту…"
                className="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-5 text-slate-900 placeholder:text-slate-400"
              />
              <button
                type="button"
                onClick={submitDraft}
                disabled={!draft.trim() || sending}
                aria-label="Отправить сообщение"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#5B50EA] text-white shadow-sm transition hover:bg-[#4F45DB] disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <SendHorizontal className="h-4 w-4" />}
              </button>
            </div>
            <div className="mt-1.5 flex items-center justify-between px-1 text-[10px] font-medium text-slate-400">
              <span>Enter — отправить · Shift+Enter — новая строка</span>
              {draft.length > 3500 && <span>{draft.length}/4096</span>}
            </div>
          </>
        )}
      </div>
    </main>
  );
}

function ConversationDetails({
  conversation,
  meta,
  statusOptions,
  now,
  controlBusy,
  statusBusy,
  onClose,
  onControl,
  onStatus,
}: {
  conversation: Conversation;
  meta: WorkspaceMeta | null;
  statusOptions: Array<{ value: string; label: string; color?: string }>;
  now: number;
  controlBusy: boolean;
  statusBusy: boolean;
  onClose?: () => void;
  onControl: (mode: "human" | "ai") => void;
  onStatus: (status: ConversationStatus) => void;
}) {
  const lease = formatLease(conversation.resume_at, now);
  const canReply = conversation.can_reply !== false;
  const primaryControlTarget =
    conversation.control_mode === "ai"
      ? "human"
      : conversation.control_mode === "paused"
        ? "human"
        : conversation.ai_available
          ? "ai"
          : null;
  const baseUrl = meta?.bitrix_base_url?.replace(/\/+$/, "");
  const dealUrl =
    baseUrl && conversation.deal_id
      ? `${baseUrl}/crm/deal/details/${encodeURIComponent(String(conversation.deal_id))}/`
      : null;

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex h-[72px] shrink-0 items-center justify-between border-b border-slate-100 px-5">
        <h2 className="text-sm font-black text-slate-900">Карточка обращения</h2>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть детали"
            className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="text-center">
          <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-sky-100 to-violet-100 text-lg font-black text-violet-700">
            {initials(conversation.display_name || conversation.username || "Telegram")}
          </span>
          <div className="mt-3 truncate text-base font-black text-slate-950">
            {conversation.display_name || conversation.username || "Клиент Telegram"}
          </div>
          <div className="mt-1 text-xs font-semibold text-slate-400">
            {conversation.username ? `@${conversation.username.replace(/^@/, "")}` : "username не указан"}
          </div>
          {conversation.external_user_id !== null &&
            conversation.external_user_id !== undefined && (
              <div className="mt-1 text-[11px] font-semibold text-slate-400">
                Telegram ID {conversation.external_user_id}
              </div>
            )}
        </div>

        <div className="mt-5 rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-sky-50 text-[9px] font-black text-sky-600">
                TG
              </span>
              <div>
                <div className="text-xs font-black text-slate-800">Telegram</div>
                <div className="mt-0.5 text-[10px] font-semibold text-slate-400">Источник обращения</div>
              </div>
            </div>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 text-[10px] font-bold",
                meta?.telegram_connected === false ? "text-red-600" : "text-emerald-600",
              )}
            >
              {meta?.telegram_connected === false ? <WifiOff className="h-3.5 w-3.5" /> : <Wifi className="h-3.5 w-3.5" />}
              {meta?.telegram_connected === false ? "Нет связи" : "Подключён"}
            </span>
          </div>
        </div>

        <section className="mt-6">
          <label className="mb-2 block text-[10px] font-black uppercase tracking-[0.12em] text-slate-400">
            Операционный статус обращения
          </label>
          <div className="relative">
            <select
              value={conversation.status}
              disabled={statusBusy}
              onChange={(event) => onStatus(event.target.value)}
              className="h-11 w-full appearance-none rounded-xl border border-slate-200 bg-white px-3 pr-10 text-xs font-bold text-slate-700 disabled:opacity-60"
            >
              {!statusOptions.some((option) => option.value === conversation.status) && (
                <option value={conversation.status}>{STATUS_LABELS[conversation.status] || conversation.status}</option>
              )}
              {statusOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {statusBusy ? (
              <Loader2 className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-slate-400" />
            ) : (
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            )}
          </div>
        </section>

        <section className="mt-6 rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-400">
              Управление диалогом
            </div>
            <ControlBadge conversation={conversation} now={now} compact />
          </div>
          <div className="mt-3 text-xs leading-5 text-slate-500">
            {!canReply
              ? "Ответ закрыт: дождитесь нового входящего сообщения клиента."
              : conversation.control_mode === "ai"
              ? "Агент видит новые сообщения и отвечает клиенту по сценарию."
              : conversation.control_mode === "paused"
                ? "Ответы приостановлены. Можно взять диалог себе или подключить разрешённого ИИ."
              : lease.remaining > 0
                ? `Агент молчит ещё ${lease.text}. Ответ менеджера начинает двухминутный интервал заново.`
                : "Ручной интервал закончился. ИИ скоро продолжит диалог."}
          </div>
          <button
            type="button"
            onClick={() => primaryControlTarget && onControl(primaryControlTarget)}
            disabled={controlBusy || !canReply || !primaryControlTarget}
            className={cn(
              "mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl text-xs font-bold transition disabled:opacity-60",
              conversation.control_mode === "ai"
                ? "bg-slate-950 text-white hover:bg-slate-800"
                : "border border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100",
            )}
          >
            {controlBusy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : conversation.control_mode === "ai" ? (
              <UserRound className="h-4 w-4" />
            ) : (
              <Bot className="h-4 w-4" />
            )}
            {conversation.control_mode === "ai"
              ? "Забрать диалог"
              : conversation.control_mode === "paused"
                ? "Взять диалог себе"
                : conversation.ai_available
                  ? "Вернуть управление ИИ"
                  : "ИИ недоступен для этого теста"}
          </button>
          {canReply && conversation.control_mode === "paused" && conversation.ai_available && (
            <button
              type="button"
              onClick={() => onControl("ai")}
              disabled={controlBusy}
              className="mt-2 inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 text-xs font-bold text-violet-700 disabled:opacity-60"
            >
              <Bot className="h-4 w-4" />
              Подключить ИИ
            </button>
          )}
        </section>

        <section className="mt-6">
          <div className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-400">
            Сделка
          </div>
          <div className="mt-2 rounded-2xl border border-slate-200 p-4">
            {conversation.deal_id ? (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-black text-slate-900">
                      {conversation.deal_title || `Сделка #${conversation.deal_id}`}
                    </div>
                    <div className="mt-1 text-[11px] font-semibold text-slate-400">
                      ID {conversation.deal_id}
                    </div>
                  </div>
                  {dealUrl && (
                    <a
                      href={dealUrl}
                      target="_blank"
                      rel="noreferrer"
                      aria-label="Открыть сделку в CRM"
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500 hover:bg-slate-200"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
                <div className="mt-4 grid grid-cols-1 gap-3">
                  <div>
                    <div className="text-[10px] font-bold uppercase text-slate-400">Этап</div>
                    <div className="mt-1 text-xs font-bold text-slate-700">
                      {conversation.stage_name || conversation.stage_id || "Не указан"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase text-slate-400">Ответственный</div>
                    <div className="mt-1 text-xs font-bold text-slate-700">
                      {conversation.assigned_to || "Не назначен"}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="flex items-start gap-3 text-xs leading-5 text-slate-500">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                <span>CRM-сделка ещё создаётся или не была привязана к обращению.</span>
              </div>
            )}
          </div>
        </section>

        <section className="mt-6 rounded-2xl bg-slate-50 p-4">
          <div className="flex items-center gap-2 text-xs font-black text-slate-700">
            <ShieldCheck className="h-4 w-4 text-violet-500" />
            Кто отправил сообщение
          </div>
          <p className="mt-2 text-[11px] leading-5 text-slate-500">
            В рабочем пространстве ответы ИИ и менеджера отмечены отдельно. Клиент получает их в том
            же Telegram-диалоге.
          </p>
        </section>
      </div>
    </div>
  );
}

function Toast({
  message,
  tone,
  onClose,
}: {
  message: string;
  tone: "error" | "success" | "info";
  onClose: () => void;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "fixed bottom-5 left-1/2 z-[80] flex max-w-[calc(100vw-32px)] -translate-x-1/2 items-center gap-3 rounded-2xl border bg-white px-4 py-3 text-sm font-semibold shadow-2xl",
        tone === "error"
          ? "border-red-200 text-red-700"
          : tone === "success"
            ? "border-emerald-200 text-emerald-700"
            : "border-slate-200 text-slate-700",
      )}
    >
      {tone === "error" ? (
        <AlertCircle className="h-4 w-4 shrink-0" />
      ) : tone === "success" ? (
        <Check className="h-4 w-4 shrink-0" />
      ) : (
        <Info className="h-4 w-4 shrink-0" />
      )}
      <span>{message}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label="Закрыть уведомление"
        className="ml-1 text-current opacity-50 hover:opacity-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

function OperatorWorkspace({
  session,
  onSessionExpired,
  onLogout,
}: {
  session: WorkspaceSession;
  onSessionExpired: () => void;
  onLogout: () => void;
}) {
  const [meta, setMeta] = useState<WorkspaceMeta | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationTotal, setConversationTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<Conversation["id"] | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [listLoading, setListLoading] = useState(true);
  const [listLoadingMore, setListLoadingMore] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [sending, setSending] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [toast, setToast] = useState<{
    message: string;
    tone: "error" | "success" | "info";
  } | null>(null);
  const selectedIdRef = useRef<Conversation["id"] | null>(null);
  const messagesRef = useRef<ConversationMessage[]>([]);
  const mobilePaneRef = useRef<MobilePane>("list");
  const listRequestSequenceRef = useRef(0);
  const messageRequestSequenceRef = useRef(0);

  const selectedConversation = useMemo(
    () =>
      conversations.find(
        (conversation) => stringId(conversation.id) === stringId(selectedId),
      ) || null,
    [conversations, selectedId],
  );

  const statusOptions = useMemo(() => {
    const fromMeta = Array.isArray(meta?.statuses)
      ? meta.statuses
          .map((item) => ({
            value: String(item.value || item.id || "").trim(),
            label: String(item.label || item.name || item.value || item.id || "").trim(),
            color: item.color,
          }))
          .filter((item) => item.value && item.label)
      : [];
    const base = fromMeta.length ? fromMeta : FALLBACK_STATUSES;
    const values = new Set(base.map((item) => item.value));
    const discovered = conversations
      .filter((conversation) => conversation.status && !values.has(conversation.status))
      .map((conversation) => ({
        value: conversation.status,
        label: STATUS_LABELS[conversation.status] || conversation.status,
      }));
    return [...base, ...discovered];
  }, [meta, conversations]);

  const reportError = useCallback(
    (error: unknown) => {
      if (isUnauthorized(error)) {
        onSessionExpired();
        return;
      }
      setToast({ message: errorMessage(error), tone: "error" });
    },
    [onSessionExpired],
  );

  const csrfToken = useCallback(() => {
    if (!session.csrf_token) {
      throw new Error("Защитная сессия не готова. Обновите страницу.");
    }
    return session.csrf_token;
  }, [session.csrf_token]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    mobilePaneRef.current = mobilePane;
  }, [mobilePane]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const loadMeta = useCallback(async () => {
    try {
      const payload = await funnelWorkspaceApi.getMeta();
      setMeta(payload);
    } catch (error) {
      reportError(error);
    }
  }, [reportError]);

  const loadConversations = useCallback(
    async (quiet = false, replaceAll = false) => {
      const sequence = ++listRequestSequenceRef.current;
      if (!quiet) setListLoading(true);
      try {
        const payload = await funnelWorkspaceApi.getConversations({
          q: query,
          status,
          limit: 100,
          offset: 0,
        });
        if (sequence !== listRequestSequenceRef.current) return;
        const rows = Array.isArray(payload.conversations) ? payload.conversations : [];
        setConversationTotal(Number(payload.total) || rows.length);
        setConversations((current) => {
          if (replaceAll || current.length <= rows.length) return rows;
          const firstPageIds = new Set(rows.map((row) => stringId(row.id)));
          const tail = current
            .slice(rows.length)
            .filter((row) => !firstPageIds.has(stringId(row.id)));
          return [...rows, ...tail].slice(0, Math.max(rows.length, Number(payload.total) || 0));
        });
        setSelectedId((current) => {
          if (
            current !== null &&
            (!replaceAll || rows.some((row) => stringId(row.id) === stringId(current)))
          ) {
            return current;
          }
          return rows[0]?.id ?? null;
        });
      } catch (error) {
        if (sequence !== listRequestSequenceRef.current) return;
        reportError(error);
      } finally {
        if (!quiet) setListLoading(false);
      }
    },
    [query, reportError, status],
  );

  const loadMoreConversations = useCallback(async () => {
    if (listLoadingMore || conversations.length >= conversationTotal) return;
    setListLoadingMore(true);
    const sequence = ++listRequestSequenceRef.current;
    try {
      const payload = await funnelWorkspaceApi.getConversations({
        q: query,
        status,
        limit: 100,
        offset: conversations.length,
      });
      if (sequence !== listRequestSequenceRef.current) return;
      const rows = Array.isArray(payload.conversations) ? payload.conversations : [];
      setConversationTotal(Number(payload.total) || conversations.length + rows.length);
      setConversations((current) => {
        const byId = new Map(current.map((item) => [stringId(item.id), item]));
        rows.forEach((item) => byId.set(stringId(item.id), item));
        return Array.from(byId.values());
      });
    } catch (error) {
      if (sequence === listRequestSequenceRef.current) reportError(error);
    } finally {
      setListLoadingMore(false);
    }
  }, [
    conversationTotal,
    conversations,
    listLoadingMore,
    query,
    reportError,
    status,
  ]);

  const fetchMessages = useCallback(
    async (
      conversationId: Conversation["id"],
      options: { reset?: boolean; showLoading?: boolean; beforeId?: number } = {},
    ) => {
      const requestKey = stringId(conversationId);
      const reset = Boolean(options.reset);
      const sequence = ++messageRequestSequenceRef.current;
      if (options.showLoading) setMessagesLoading(true);
      try {
        const existing = reset ? [] : messagesRef.current.filter((message) => !message.optimistic && message.id > 0);
        const afterId = existing.length
          ? Math.max(...existing.map((message) => message.id))
          : undefined;
        const payload = await funnelWorkspaceApi.getMessages(
          conversationId,
          options.beforeId
            ? { beforeId: options.beforeId, limit: 200 }
            : reset
              ? { limit: 200 }
              : { afterId, limit: 200 },
        );
        if (
          sequence !== messageRequestSequenceRef.current ||
          stringId(selectedIdRef.current) !== requestKey
        ) {
          return;
        }
        const incoming = Array.isArray(payload.messages) ? payload.messages : [];
        if (reset || options.beforeId) {
          setHasOlderMessages(Boolean(payload.has_more_before));
        }
        setMessages((current) => {
          if (reset) {
            return [...incoming].sort((left, right) => left.id - right.id);
          }
          const incomingKeys = new Set(
            incoming
              .map((message) => message.idempotency_key)
              .filter((key): key is string => Boolean(key)),
          );
          const byId = new Map<number, ConversationMessage>();
          current
            .filter(
              (message) =>
                !(
                  message.id < 0 &&
                  message.idempotency_key &&
                  incomingKeys.has(message.idempotency_key)
                ),
            )
            .forEach((message) => byId.set(message.id, message));
          incoming.forEach((message) => byId.set(message.id, message));
          return Array.from(byId.values()).sort((left, right) => {
            const timeDelta = parseTimestamp(left.created_at) - parseTimestamp(right.created_at);
            return timeDelta || left.id - right.id;
          });
        });

        const visibleMessages =
          reset || options.beforeId
            ? incoming
            : [...messagesRef.current, ...incoming];
        const throughMessageId = visibleMessages.reduce(
          (maximum, message) => Math.max(maximum, Number(message.id) || 0),
          0,
        );
        const desktopVisible =
          typeof window !== "undefined" &&
          window.matchMedia("(min-width: 768px)").matches;
        const chatVisible =
          typeof document !== "undefined" &&
          document.visibilityState === "visible" &&
          (desktopVisible || mobilePaneRef.current === "chat");
        if (!options.beforeId && throughMessageId > 0 && chatVisible) {
          void funnelWorkspaceApi
            .markRead(conversationId, throughMessageId, csrfToken())
            .then((result) => {
              const marked = result.conversation as Partial<Conversation> | undefined;
              if (!marked || stringId(selectedIdRef.current) !== requestKey) return;
              setConversations((current) =>
                current.map((conversation) =>
                  stringId(conversation.id) === requestKey
                    ? {
                        ...conversation,
                        unread_count:
                          typeof marked.unread_count === "number"
                            ? marked.unread_count
                            : conversation.unread_count,
                      }
                    : conversation,
                ),
              );
            })
            .catch(reportError);
        }
      } catch (error) {
        if (sequence === messageRequestSequenceRef.current) reportError(error);
      } finally {
        if (options.showLoading) setMessagesLoading(false);
      }
    },
    [csrfToken, reportError],
  );

  useEffect(() => {
    void loadMeta();
    const timer = window.setInterval(() => void loadMeta(), 30_000);
    return () => window.clearInterval(timer);
  }, [loadMeta]);

  useEffect(() => {
    setConversations([]);
    setConversationTotal(0);
    setSelectedId(null);
    void loadConversations(false, true);
    const timer = window.setInterval(() => void loadConversations(true), 4_000);
    return () => window.clearInterval(timer);
  }, [loadConversations]);

  useEffect(() => {
    if (selectedId === null) {
      messageRequestSequenceRef.current += 1;
      setMessages([]);
      setHasOlderMessages(false);
      return;
    }
    selectedIdRef.current = selectedId;
    setMessages([]);
    setHasOlderMessages(false);
    void fetchMessages(selectedId, { reset: true, showLoading: true });

    const timer = window.setInterval(() => {
      void fetchMessages(selectedId);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [fetchMessages, selectedId]);

  const chooseConversation = (conversation: Conversation) => {
    const alreadySelected =
      stringId(selectedIdRef.current) === stringId(conversation.id);
    setSelectedId(conversation.id);
    mobilePaneRef.current = "chat";
    setMobilePane("chat");
    if (alreadySelected) void fetchMessages(conversation.id);
  };

  const refreshSelected = useCallback(async () => {
    await loadConversations(true);
    const id = selectedIdRef.current;
    if (id !== null) await fetchMessages(id);
  }, [fetchMessages, loadConversations]);

  const sendMessage = async (text: string, retryMessage?: ConversationMessage) => {
    const conversation = selectedConversation;
    if (!conversation || sending) return;
    if (conversation.can_reply === false) {
      setToast({
        message: "Окно ответа Telegram закрыто. Дождитесь нового сообщения клиента.",
        tone: "info",
      });
      return;
    }
    if (conversation.control_mode !== "human") {
      setToast({ message: "Сначала возьмите диалог в ручной режим.", tone: "info" });
      return;
    }

    const optimisticId = -Date.now();
    const reusableRequestKey =
      retryMessage && Number(retryMessage.id) < 0
        ? retryMessage.idempotency_key
        : null;
    const idempotencyKey =
      reusableRequestKey ||
      (typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`);
    const optimistic: ConversationMessage = {
      id: optimisticId,
      created_at: new Date().toISOString(),
      author_type: "human",
      author_name: "Менеджер",
      direction: "outgoing",
      text,
      delivery_status: "sending",
      error: null,
      idempotency_key: idempotencyKey,
      optimistic: true,
    };

    if (retryMessage) {
      setMessages((current) => current.filter((message) => message.id !== retryMessage.id));
    }
    setMessages((current) => [...current, optimistic]);
    setSending(true);
    try {
      await funnelWorkspaceApi.sendMessage(conversation.id, {
        text,
        expected_version: conversation.state_version,
        csrf_token: csrfToken(),
        idempotency_key: idempotencyKey,
      });
      setMessages((current) => current.filter((message) => message.id !== optimisticId));
      await refreshSelected();
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === optimisticId
            ? {
                ...message,
                optimistic: false,
                delivery_status: "failed",
                error: errorMessage(error),
              }
            : message,
        ),
      );
      if (error instanceof FunnelWorkspaceApiError && error.status === 409) {
        await refreshSelected();
      }
      reportError(error);
    } finally {
      setSending(false);
    }
  };

  const setControl = async (mode: "human" | "ai") => {
    const conversation = selectedConversation;
    if (!conversation || controlBusy || conversation.control_mode === mode) return;
    if (conversation.can_reply === false) {
      setToast({
        message: "Управление недоступно до нового сообщения клиента.",
        tone: "info",
      });
      return;
    }
    if (mode === "ai" && !conversation.ai_available) {
      setToast({
        message: "ИИ пока не разрешён для этого тестового Telegram-диалога.",
        tone: "info",
      });
      return;
    }
    setControlBusy(true);
    try {
      await funnelWorkspaceApi.setControl(conversation.id, {
        mode,
        expected_version: conversation.state_version,
        csrf_token: csrfToken(),
      });
      await refreshSelected();
      setToast({
        message:
          mode === "human"
            ? "ИИ остановлен на 2 минуты. Можно отвечать клиенту."
            : "Управление возвращено ИИ-агенту.",
        tone: "success",
      });
    } catch (error) {
      if (error instanceof FunnelWorkspaceApiError && error.status === 409) {
        await refreshSelected();
      }
      reportError(error);
    } finally {
      setControlBusy(false);
    }
  };

  const setConversationStatus = async (nextStatus: ConversationStatus) => {
    const conversation = selectedConversation;
    if (!conversation || statusBusy || nextStatus === conversation.status) return;
    setStatusBusy(true);
    try {
      await funnelWorkspaceApi.setStatus(conversation.id, {
        status: nextStatus,
        expected_version: conversation.state_version,
        csrf_token: csrfToken(),
      });
      await loadConversations(true);
      setToast({ message: "Статус заявки обновлён.", tone: "success" });
    } catch (error) {
      if (error instanceof FunnelWorkspaceApiError && error.status === 409) {
        await loadConversations(true);
      }
      reportError(error);
    } finally {
      setStatusBusy(false);
    }
  };

  const logout = async () => {
    if (loggingOut) return;
    if (session.admin_session) {
      window.location.assign("/");
      return;
    }
    setLoggingOut(true);
    try {
      await funnelWorkspaceApi.logout(csrfToken());
      onLogout();
    } catch (error) {
      if (isUnauthorized(error)) {
        onLogout();
      } else {
        reportError(error);
      }
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <div className="flex h-[100dvh] min-h-0 flex-col overflow-hidden bg-[#F4F6FA] font-sans text-slate-800">
      <header className="flex h-[66px] shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-4 sm:gap-7">
          <div className="flex shrink-0 items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-[#7167FF] to-[#4E43E8] text-sm font-black text-white shadow-md shadow-violet-200">
              A
            </span>
            <div className="hidden sm:block">
              <div className="text-sm font-black leading-tight text-slate-950">Alberi</div>
              <div className="text-[10px] font-semibold leading-tight text-slate-400">Воронки</div>
            </div>
          </div>

          <div className="inline-flex h-9 min-w-0 items-center gap-2 rounded-xl bg-slate-100 px-3 text-xs font-bold text-slate-700 sm:px-4">
            <MessageCircleMore className="h-4 w-4 shrink-0 text-violet-600" />
            <span className="truncate">Обращения</span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span
            className={cn(
              "hidden items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[10px] font-bold md:inline-flex",
              meta?.telegram_connected === false
                ? "bg-red-50 text-red-600"
                : "bg-emerald-50 text-emerald-700",
            )}
          >
            {meta?.telegram_connected === false ? <WifiOff className="h-3 w-3" /> : <Wifi className="h-3 w-3" />}
            Telegram
          </span>
          <button
            type="button"
            onClick={() => void logout()}
            disabled={loggingOut}
            aria-label="Выйти"
            className="flex h-9 items-center gap-2 rounded-xl border border-slate-200 px-2.5 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-slate-700 disabled:opacity-60 sm:px-3"
          >
            {loggingOut ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
            <span className="hidden sm:inline">
              {session.admin_session ? "В кабинет" : "Выйти"}
            </span>
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[350px_minmax(0,1fr)_320px]">
          <div className={cn("h-full min-h-0", mobilePane === "chat" ? "hidden md:block" : "block")}>
            <ConversationList
              conversations={conversations}
              selectedId={selectedId}
              loading={listLoading}
              loadingMore={listLoadingMore}
              total={conversationTotal}
              query={queryInput}
              status={status}
              statusOptions={statusOptions}
              now={now}
              onQueryChange={setQueryInput}
              onStatusChange={setStatus}
              onSelect={chooseConversation}
              onRefresh={() => void loadConversations(false)}
              onLoadMore={() => void loadMoreConversations()}
            />
          </div>

          <div className={cn("min-h-0 min-w-0", mobilePane === "list" ? "hidden md:flex" : "flex")}>
            <ChatPanel
              conversation={selectedConversation}
              messages={messages}
              loading={messagesLoading}
              loadingOlder={loadingOlder}
              hasOlderMessages={hasOlderMessages}
              sending={sending}
              controlBusy={controlBusy}
              now={now}
              onBack={() => {
                mobilePaneRef.current = "list";
                setMobilePane("list");
              }}
              onShowDetails={() => setDetailsOpen(true)}
              onLoadOlder={() => {
                if (selectedId === null || loadingOlder) return;
                const oldestId = messagesRef.current
                  .filter((message) => message.id > 0)
                  .reduce(
                    (minimum, message) => Math.min(minimum, message.id),
                    Number.POSITIVE_INFINITY,
                  );
                if (!Number.isFinite(oldestId)) return;
                setLoadingOlder(true);
                void fetchMessages(selectedId, { beforeId: oldestId }).finally(() =>
                  setLoadingOlder(false),
                );
              }}
              onSend={(text) => void sendMessage(text)}
              onRetry={(message) => void sendMessage(message.text, message)}
              onControl={(mode) => void setControl(mode)}
            />
          </div>

          <aside className="hidden min-h-0 border-l border-slate-200 xl:block">
            {selectedConversation ? (
              <ConversationDetails
                conversation={selectedConversation}
                meta={meta}
                statusOptions={statusOptions}
                now={now}
                controlBusy={controlBusy}
                statusBusy={statusBusy}
                onControl={(mode) => void setControl(mode)}
                onStatus={(nextStatus) => void setConversationStatus(nextStatus)}
              />
            ) : (
              <div className="flex h-full items-center justify-center bg-white px-8 text-center text-xs font-semibold leading-5 text-slate-400">
                Выберите обращение, чтобы увидеть карточку клиента
              </div>
            )}
          </aside>
      </div>

      {detailsOpen && selectedConversation && (
        <div className="fixed inset-0 z-50 xl:hidden">
          <button
            type="button"
            aria-label="Закрыть детали"
            onClick={() => setDetailsOpen(false)}
            className="absolute inset-0 h-full w-full bg-slate-950/35 backdrop-blur-[2px]"
          />
          <aside className="absolute inset-y-0 right-0 w-full max-w-[360px] shadow-2xl">
            <ConversationDetails
              conversation={selectedConversation}
              meta={meta}
              statusOptions={statusOptions}
              now={now}
              controlBusy={controlBusy}
              statusBusy={statusBusy}
              onClose={() => setDetailsOpen(false)}
              onControl={(mode) => void setControl(mode)}
              onStatus={(nextStatus) => void setConversationStatus(nextStatus)}
            />
          </aside>
        </div>
      )}

      {toast && (
        <Toast
          message={toast.message}
          tone={toast.tone}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}

export function FunnelWorkspace() {
  const [session, setSession] = useState<WorkspaceSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState("");
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setBootError("");
    void funnelWorkspaceApi
      .getSession()
      .then((payload) => {
        if (active) setSession(payload);
      })
      .catch((error) => {
        if (active) setBootError(errorMessage(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [retryToken]);

  if (loading) return <LoadingScreen />;
  if (bootError) {
    return <SessionError message={bootError} onRetry={() => setRetryToken((value) => value + 1)} />;
  }
  if (
    session?.authenticated &&
    session.configured === false &&
    session.can_configure
  ) {
    return (
      <WorkspacePasswordSetup
        session={session}
        onConfigured={() =>
          setSession((current) =>
            current
              ? {
                  ...current,
                  configured: true,
                }
              : current,
          )
        }
      />
    );
  }
  if (!session?.authenticated) {
    return (
      <WorkspaceLogin
        configured={session?.configured !== false}
        onAuthenticated={(nextSession) =>
          setSession({
            configured: nextSession.configured ?? session?.configured ?? true,
            ...nextSession,
          })
        }
      />
    );
  }
  if (session.workspace_enabled === false) {
    return (
      <WorkspaceDisabled
        session={session}
        onLogout={() =>
          setSession({
            authenticated: false,
            configured: true,
            workspace_enabled: false,
          })
        }
      />
    );
  }

  return (
    <OperatorWorkspace
      session={session}
      onSessionExpired={() =>
        setSession({
          authenticated: false,
          configured: true,
        })
      }
      onLogout={() =>
        setSession({
          authenticated: false,
          configured: true,
        })
      }
    />
  );
}
