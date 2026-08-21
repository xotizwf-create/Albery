import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCheck,
  Clock,
  ExternalLink,
  Loader2,
  Pause,
  PenLine,
  Plus,
  RefreshCw,
  Search,
  Send,
  User,
} from "lucide-react";

import { cn } from "../lib/utils";
import { AvitoApiError, avitoApi } from "./api";
import type {
  AvitoAccount,
  AvitoChannelState,
  AvitoConversation,
  AvitoMessage,
  AvitoSessionStatus,
} from "./types";

const STATE_POLL_MS = 30_000;
const LIST_POLL_MS = 4_000;
const MESSAGES_POLL_MS = 3_000;

const SESSION_LABELS: Record<AvitoSessionStatus, { text: string; tone: string }> = {
  ok: { text: "сессия жива", tone: "bg-emerald-50 text-emerald-700" },
  needs_login: { text: "нужен вход", tone: "bg-amber-50 text-amber-700" },
  blocked: { text: "заблокирован", tone: "bg-rose-50 text-rose-700" },
  error: { text: "ошибка сессии", tone: "bg-rose-50 text-rose-700" },
  unknown: { text: "не проверена", tone: "bg-slate-100 text-slate-600" },
};

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "new", label: "Новые" },
  { value: "open", label: "В работе" },
  { value: "waiting", label: "Ждут нас" },
  { value: "closed", label: "Закрытые" },
];

const CONTROL_LABELS: Record<AvitoConversation["control_mode"], { text: string; tone: string }> = {
  ai: { text: "ИИ управляет", tone: "bg-[#EEF2FF] text-[#5440F6]" },
  human: { text: "Человек управляет", tone: "bg-amber-50 text-amber-700" },
  paused: { text: "Ответы на паузе", tone: "bg-slate-100 text-slate-600" },
};

const timeLabel = (iso: string | null): string => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const sameDay =
    date.getDate() === today.getDate() &&
    date.getMonth() === today.getMonth() &&
    date.getFullYear() === today.getFullYear();
  return sameDay
    ? date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) +
        " " +
        date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};

const conversationIdFromPath = (): number | null => {
  const match = window.location.pathname.match(/^\/avito\/(\d+)/);
  return match ? Number(match[1]) : null;
};

const operatorName = (): string => {
  const stored = window.localStorage.getItem("avito.operator_name");
  return (stored || "Оператор").slice(0, 80);
};

export function AvitoInbox() {
  const [state, setState] = useState<AvitoChannelState | null>(null);
  const [conversations, setConversations] = useState<AvitoConversation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(conversationIdFromPath());
  const [selected, setSelected] = useState<AvitoConversation | null>(null);
  const [messages, setMessages] = useState<AvitoMessage[]>([]);
  const [account, setAccount] = useState<string>("");
  const [status, setStatus] = useState<string>("all");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAccountForm, setShowAccountForm] = useState(false);
  const [newAccount, setNewAccount] = useState({ slug: "", label: "", egress_label: "" });
  const [showOutreach, setShowOutreach] = useState(false);
  const [outreach, setOutreach] = useState({ item_url: "", text: "" });
  const [outreachSending, setOutreachSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const report = useCallback((err: unknown) => {
    setError(err instanceof AvitoApiError ? err.message : "Неожиданная ошибка интерфейса.");
  }, []);

  const loadState = useCallback(async () => {
    try {
      setState(await avitoApi.getState());
    } catch (err) {
      report(err);
    }
  }, [report]);

  const loadConversations = useCallback(async () => {
    try {
      const payload = await avitoApi.getConversations({ account, status, q: query });
      setConversations(payload.conversations);
      setError(null);
    } catch (err) {
      report(err);
    } finally {
      setLoading(false);
    }
  }, [account, status, query, report]);

  const loadMessages = useCallback(
    async (conversationId: number, incremental: boolean) => {
      try {
        const afterId = incremental ? messages.at(-1)?.id ?? 0 : 0;
        const payload = await avitoApi.getMessages(conversationId, afterId);
        setSelected(payload.conversation);
        setMessages((prev) =>
          afterId > 0 ? [...prev, ...payload.messages.filter((m) => m.id > afterId)] : payload.messages,
        );
      } catch (err) {
        report(err);
      }
    },
    [messages, report],
  );

  useEffect(() => {
    void loadState();
    const timer = window.setInterval(() => void loadState(), STATE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadState]);

  useEffect(() => {
    void loadConversations();
    const timer = window.setInterval(() => void loadConversations(), LIST_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadConversations]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null);
      setMessages([]);
      return;
    }
    void loadMessages(selectedId, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    if (selectedId === null) return;
    const timer = window.setInterval(() => void loadMessages(selectedId, true), MESSAGES_POLL_MS);
    return () => window.clearInterval(timer);
  }, [selectedId, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  // Прочитанное отмечаем по последнему видимому сообщению — счётчик слева обнуляется сам.
  useEffect(() => {
    const last = messages.at(-1);
    if (!selected || !last || selected.last_read_message_id >= last.id) return;
    void avitoApi.markRead(selected.id, last.id).catch(() => undefined);
  }, [messages, selected]);

  const openConversation = (conversation: AvitoConversation) => {
    setSelectedId(conversation.id);
    setSelected(conversation);
    setMessages([]);
    window.history.pushState({}, "", `/avito/${conversation.id}`);
  };

  const closeConversation = () => {
    setSelectedId(null);
    window.history.pushState({}, "", "/avito");
  };

  const accounts = state?.accounts ?? [];
  const transportOff = state ? !state.transport_enabled : false;
  const activeAccount = useMemo(
    () => accounts.find((a) => a.slug === account) ?? null,
    [accounts, account],
  );

  const blockedReason = useMemo(() => {
    if (!selected) return null;
    if (transportOff) return "Транспорт Авито выключен — сообщение никто не доставит.";
    const own = accounts.find((a) => a.slug === selected.account_slug);
    if (!own) return `Аккаунт «${selected.account_slug || "—"}» не зарегистрирован в канале.`;
    if (!own.is_active) return `Аккаунт «${own.label}» выключен.`;
    if (own.session_status !== "ok") {
      return `Сессия аккаунта «${own.label}»: ${SESSION_LABELS[own.session_status].text}.`;
    }
    return null;
  }, [selected, accounts, transportOff]);

  const send = async () => {
    if (!selected || !draft.trim() || sending) return;
    setSending(true);
    try {
      const payload = await avitoApi.reply(selected.id, {
        text: draft.trim(),
        expected_version: selected.state_version,
        operator_name: operatorName(),
        idempotency_key: `avito-ui-${selected.id}-${Date.now()}`,
      });
      setDraft("");
      setSelected(payload.conversation);
      setNotice("Ответ поставлен в очередь отправки.");
      await loadMessages(selected.id, true);
    } catch (err) {
      report(err);
    } finally {
      setSending(false);
    }
  };

  const changeControl = async (mode: "ai" | "human" | "paused", permanent = false) => {
    if (!selected) return;
    try {
      const payload = await avitoApi.setControl(selected.id, {
        mode,
        permanent,
        expected_version: selected.state_version,
        operator_name: operatorName(),
      });
      setSelected(payload.conversation);
      await loadConversations();
    } catch (err) {
      report(err);
    }
  };

  const addAccount = async () => {
    try {
      // Код не передаём: сервер выведет его из названия одним способом для всех входов.
      const created = await avitoApi.createAccount({
        label: newAccount.label.trim(),
        egress_label: newAccount.egress_label.trim(),
      });
      setNewAccount({ slug: "", label: "", egress_label: "" });
      setShowAccountForm(false);
      setAccount(created.account.slug);
      setNotice(
        'Аккаунт добавлен. Теперь нажмите «Войти в Авито» — на компьютере, где стоит ' +
          "зеркало, откроется окно браузера.",
      );
      await loadState();
    } catch (err) {
      report(err);
    }
  };

  const removeAccount = async (item: AvitoAccount) => {
    if (!window.confirm(`Удалить аккаунт «${item.label}»?`)) return;
    try {
      await avitoApi.deleteAccount(item.slug);
      setNotice(`Аккаунт «${item.label}» удалён.`);
      setAccount("");
      await loadState();
    } catch (err) {
      // 409 — у аккаунта есть переписка. Сервер прислал, сколько именно: спрашиваем
      // ещё раз, уже с числом, и только потом уносим разговоры вместе с аккаунтом.
      const conflict = err instanceof AvitoApiError && err.status === 409;
      if (!conflict) {
        report(err);
        return;
      }
      if (!window.confirm(`${err.message}\n\nУдалить аккаунт вместе с перепиской?`)) return;
      try {
        await avitoApi.deleteAccount(item.slug, true);
        setNotice(`Аккаунт «${item.label}» удалён вместе с перепиской.`);
        setAccount("");
        await loadState();
      } catch (second) {
        report(second);
      }
    }
  };

  // Кабинет — страница на сервере, открыть браузер на компьютере человека он не может.
  // Поэтому кнопка не «открывает окно», а оставляет заявку: воркер, который и так работает
  // на нужной машине, откроет окно там на ближайшем обходе (не дольше 20 секунд).
  const requestLogin = async (item: AvitoAccount) => {
    try {
      await avitoApi.requestLogin(item.slug, operatorName());
      setNotice(
        `Запросили вход в «${item.label}». На компьютере, где работает зеркало, вот-вот ` +
          "откроется окно браузера: пройдите капчу, войдите и введите код из SMS. " +
          "Здесь состояние сменится на «сессия жива» само.",
      );
      await loadState();
    } catch (err) {
      report(err);
    }
  };

  const sendOutreach = async () => {
    const target = account || accounts.find((a) => a.is_active && a.session_status === "ok")?.slug || "";
    if (!target) {
      setError("Выберите аккаунт слева: писать первым можно только от живой сессии.");
      return;
    }
    setOutreachSending(true);
    try {
      const payload = await avitoApi.outreach({
        account: target,
        item_url: outreach.item_url.trim(),
        text: outreach.text.trim(),
        operator_name: operatorName(),
      });
      setOutreach({ item_url: "", text: "" });
      setShowOutreach(false);
      setNotice("Сообщение поставлено в очередь — воркер откроет объявление и напишет автору.");
      await loadConversations();
      if (payload.conversation) openConversation(payload.conversation);
    } catch (err) {
      report(err);
    } finally {
      setOutreachSending(false);
    }
  };

  const toggleAccount = async (item: AvitoAccount) => {
    try {
      await avitoApi.setAccountActive(item.slug, !item.is_active);
      await loadState();
    } catch (err) {
      report(err);
    }
  };

  return (
    <div className="min-h-screen bg-[#f6f8fb] font-sans text-slate-900">
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <a
              href="/main"
              className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
              title="В Центр Агента"
            >
              <ArrowLeft className="h-4 w-4" />
            </a>
            <div>
              <h1 className="text-[20px] font-black tracking-tight">Авито</h1>
              <p className="text-[13px] font-medium text-slate-500">
                Переписки с покупателями и продавцами — все аккаунты в одном окне
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4 text-[13px] font-bold text-slate-500">
            <span>
              Обращений: <span className="text-slate-900">{state?.total_conversations ?? 0}</span>
            </span>
            <span>
              Непрочитанных: <span className="text-slate-900">{state?.unread_conversations ?? 0}</span>
            </span>
            <button
              onClick={() => {
                void loadState();
                void loadConversations();
              }}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
              title="Обновить"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      {transportOff && (
        <div className="border-b border-amber-200 bg-amber-50 px-6 py-3">
          <div className="mx-auto flex max-w-[1600px] items-start gap-2 text-[13px] font-semibold text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              Транспорт Авито выключен: браузерная сессия не запущена, новые сообщения не поступают,
              отправка отклоняется. Журнал ниже показывает то, что уже сохранено.
            </p>
          </div>
        </div>
      )}

      {(error || notice) && (
        <div className="mx-auto max-w-[1600px] px-6 pt-4">
          <div
            className={cn(
              "flex items-start justify-between gap-3 rounded-xl px-4 py-3 text-[13px] font-semibold",
              error ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700",
            )}
          >
            <span>{error || notice}</span>
            <button
              onClick={() => {
                setError(null);
                setNotice(null);
              }}
              className="text-current opacity-60 transition hover:opacity-100"
            >
              скрыть
            </button>
          </div>
        </div>
      )}

      <main className="mx-auto flex max-w-[1600px] gap-4 p-6">
        <aside className="w-64 shrink-0 space-y-3">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[13px] font-black uppercase tracking-wider text-slate-400">Аккаунты</h2>
              <button
                onClick={() => setShowAccountForm((value) => !value)}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-[#5440F6] transition hover:bg-[#EEF2FF]"
                title="Добавить аккаунт"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>

            <button
              onClick={() => setAccount("")}
              className={cn(
                "mb-1 w-full rounded-xl px-3 py-2 text-left text-[14px] font-bold transition",
                account === "" ? "bg-[#EEF2FF] text-[#5440F6]" : "text-slate-600 hover:bg-slate-100",
              )}
            >
              Все аккаунты
            </button>

            {accounts.map((item) => (
              <button
                key={item.slug}
                onClick={() => setAccount(item.slug)}
                className={cn(
                  "mb-1 w-full rounded-xl px-3 py-2 text-left transition",
                  account === item.slug ? "bg-[#EEF2FF]" : "hover:bg-slate-100",
                )}
              >
                <span
                  className={cn(
                    "block truncate text-[14px] font-bold",
                    account === item.slug ? "text-[#5440F6]" : "text-slate-700",
                    !item.is_active && "line-through opacity-60",
                  )}
                >
                  {item.label}
                </span>
                <span
                  className={cn(
                    "mt-1 inline-block rounded-md px-1.5 py-0.5 text-[11px] font-bold",
                    item.login_requested_at && item.session_status !== "ok"
                      ? "bg-amber-50 text-amber-700"
                      : SESSION_LABELS[item.session_status].tone,
                  )}
                >
                  {item.login_requested_at && item.session_status !== "ok"
                    ? "ждём вход на устройстве"
                    : SESSION_LABELS[item.session_status].text}
                </span>
              </button>
            ))}

            {accounts.length === 0 && !showAccountForm && (
              <p className="px-1 py-2 text-[13px] font-medium text-slate-400">
                Аккаунтов пока нет. Добавьте первый — и воркер транспорта войдёт в него один раз
                вручную.
              </p>
            )}

            {showAccountForm && (
              <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
                <input
                  value={newAccount.label}
                  onChange={(e) => setNewAccount({ ...newAccount, label: e.target.value })}
                  placeholder="Название: Отдел закупок"
                  autoFocus
                  className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-semibold outline-none focus:border-[#5440F6]"
                />
                <input
                  value={newAccount.egress_label}
                  onChange={(e) => setNewAccount({ ...newAccount, egress_label: e.target.value })}
                  placeholder="Чей компьютер (необязательно)"
                  className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-semibold outline-none focus:border-[#5440F6]"
                />
                <p className="text-[11px] font-medium text-slate-400">
                  Код аккаунта придумывать не нужно — он получится из названия.
                </p>
                <button
                  onClick={() => void addAccount()}
                  disabled={!newAccount.label.trim()}
                  className="w-full rounded-lg bg-[#5440F6] px-3 py-2 text-[13px] font-bold text-white transition hover:bg-[#4433d6] disabled:opacity-40"
                >
                  Добавить и войти
                </button>
              </div>
            )}
          </div>

          {activeAccount && (
            <div className="rounded-2xl border border-slate-200 bg-white p-4 text-[13px]">
              <p className="font-black text-slate-900">{activeAccount.label}</p>
              <p className="mt-1 font-medium text-slate-500">
                Выход: {activeAccount.egress_label || "не указан"}
              </p>
              {activeAccount.session_checked_at && (
                <p className="mt-1 font-medium text-slate-500">
                  Проверка: {timeLabel(activeAccount.session_checked_at)}
                </p>
              )}
              {activeAccount.last_error && (
                <p className="mt-2 rounded-lg bg-rose-50 px-2 py-1.5 font-semibold text-rose-700">
                  {activeAccount.last_error}
                </p>
              )}
              {activeAccount.session_status === "ok" ? (
                <p className="mt-3 rounded-lg bg-emerald-50 px-2 py-1.5 font-semibold text-emerald-700">
                  Вход выполнен. Сессия сохранена на сервере — переживёт переустановку системы.
                </p>
              ) : (
                <>
                  <button
                    onClick={() => void requestLogin(activeAccount)}
                    className="mt-3 w-full rounded-lg bg-[#5440F6] px-3 py-2 font-bold text-white transition hover:bg-[#4433d6]"
                  >
                    {activeAccount.login_requested_at ? "Открыть окно входа ещё раз" : "Войти в Авито"}
                  </button>
                  <p className="mt-2 font-medium text-slate-500">
                    {activeAccount.login_requested_at
                      ? "Окно браузера открывается на компьютере, где работает зеркало. " +
                        "Пройдите там капчу, войдите и введите код из SMS."
                      : "На компьютере, где работает зеркало, откроется окно браузера. " +
                        "Войти можно только оттуда: Авито не пускает адреса дата-центров."}
                  </p>
                </>
              )}
              <button
                onClick={() => void toggleAccount(activeAccount)}
                className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-1.5 font-bold text-slate-600 transition hover:bg-slate-50"
              >
                {activeAccount.is_active ? "Выключить аккаунт" : "Включить аккаунт"}
              </button>
              <button
                onClick={() => void removeAccount(activeAccount)}
                className="mt-2 w-full rounded-lg border border-rose-200 px-3 py-1.5 font-bold text-rose-600 transition hover:bg-rose-50"
              >
                Удалить аккаунт
              </button>
            </div>
          )}
        </aside>

        <section className="w-[380px] shrink-0 rounded-2xl border border-slate-200 bg-white">
          <div className="border-b border-slate-100 p-4">
            <button
              onClick={() => setShowOutreach((value) => !value)}
              className="mb-3 flex w-full items-center justify-center gap-2 rounded-xl bg-[#5440F6] px-3 py-2 text-[13px] font-bold text-white transition hover:bg-[#4433d6]"
            >
              <PenLine className="h-4 w-4" /> Написать первым
            </button>

            {showOutreach && (
              <div className="mb-3 space-y-2 rounded-xl border border-slate-200 p-3">
                <input
                  value={outreach.item_url}
                  onChange={(e) => setOutreach({ ...outreach, item_url: e.target.value })}
                  placeholder="ссылка на объявление или его номер"
                  className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-semibold outline-none focus:border-[#5440F6]"
                />
                <textarea
                  value={outreach.text}
                  onChange={(e) => setOutreach({ ...outreach, text: e.target.value })}
                  rows={3}
                  placeholder="первое сообщение автору объявления"
                  className="w-full resize-none rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] font-medium outline-none focus:border-[#5440F6]"
                />
                <button
                  onClick={() => void sendOutreach()}
                  disabled={!outreach.item_url.trim() || !outreach.text.trim() || outreachSending}
                  className="w-full rounded-lg bg-[#5440F6] px-3 py-2 text-[13px] font-bold text-white transition hover:bg-[#4433d6] disabled:opacity-40"
                >
                  {outreachSending ? "Ставлю в очередь…" : "Отправить"}
                </button>
                <p className="text-[11px] font-semibold text-slate-400">
                  Разговор появится в списке сразу, сообщение уйдёт, когда воркер откроет
                  объявление. Диалог сразу под управлением человека.
                </p>
              </div>
            )}

            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={queryInput}
                onChange={(e) => setQueryInput(e.target.value)}
                placeholder="Поиск по переписке"
                className="w-full rounded-xl border border-slate-200 py-2 pl-9 pr-3 text-[14px] font-semibold outline-none focus:border-[#5440F6]"
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-1">
              {STATUS_FILTERS.map((filter) => (
                <button
                  key={filter.value}
                  onClick={() => setStatus(filter.value)}
                  className={cn(
                    "rounded-lg px-2.5 py-1 text-[12px] font-bold transition",
                    status === filter.value
                      ? "bg-[#EEF2FF] text-[#5440F6]"
                      : "text-slate-500 hover:bg-slate-100",
                  )}
                >
                  {filter.label}
                </button>
              ))}
            </div>
          </div>

          <div className="max-h-[calc(100vh-320px)] overflow-y-auto">
            {loading && (
              <div className="flex items-center justify-center gap-2 py-10 text-[13px] font-semibold text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" /> Загружаю
              </div>
            )}

            {!loading && conversations.length === 0 && (
              <p className="px-4 py-10 text-center text-[13px] font-medium text-slate-400">
                Обращений нет. Они появятся здесь, как только транспорт начнёт зеркалить переписки
                Авито.
              </p>
            )}

            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                onClick={() => openConversation(conversation)}
                className={cn(
                  "w-full border-b border-slate-50 px-4 py-3 text-left transition",
                  selectedId === conversation.id ? "bg-[#EEF2FF]" : "hover:bg-slate-50",
                )}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[14px] font-black text-slate-900">
                    {conversation.display_name}
                  </span>
                  <span className="shrink-0 text-[11px] font-bold text-slate-400">
                    {timeLabel(conversation.last_message_at)}
                  </span>
                </div>
                {conversation.listing.title && (
                  <p className="mt-0.5 truncate text-[12px] font-bold text-slate-500">
                    {conversation.listing.title}
                    {conversation.listing.price ? ` · ${conversation.listing.price}` : ""}
                  </p>
                )}
                <p className="mt-1 line-clamp-2 text-[13px] font-medium text-slate-500">
                  {conversation.last_author_type === "client" ? "" : "Мы: "}
                  {conversation.last_message_text || "—"}
                </p>
                <div className="mt-2 flex items-center gap-1.5">
                  <span
                    className={cn(
                      "rounded-md px-1.5 py-0.5 text-[11px] font-bold",
                      CONTROL_LABELS[conversation.control_mode].tone,
                    )}
                  >
                    {CONTROL_LABELS[conversation.control_mode].text}
                  </span>
                  {conversation.unread_count > 0 && (
                    <span className="rounded-md bg-[#5440F6] px-1.5 py-0.5 text-[11px] font-black text-white">
                      {conversation.unread_count}
                    </span>
                  )}
                  {account === "" && conversation.account_slug && (
                    <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-bold text-slate-600">
                      {accounts.find((a) => a.slug === conversation.account_slug)?.label ||
                        conversation.account_slug}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="flex min-h-[calc(100vh-220px)] flex-1 flex-col rounded-2xl border border-slate-200 bg-white">
          {!selected ? (
            <div className="flex flex-1 items-center justify-center px-6 text-center">
              <p className="max-w-sm text-[14px] font-semibold text-slate-400">
                Выберите обращение слева. Здесь будет вся переписка: сообщения собеседника, ответы
                оператора и ответы ИИ с состоянием доставки.
              </p>
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-4 border-b border-slate-100 p-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={closeConversation}
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                      title="Закрыть переписку"
                    >
                      <ArrowLeft className="h-4 w-4" />
                    </button>
                    <h2 className="truncate text-[16px] font-black">{selected.display_name}</h2>
                    <span
                      className={cn(
                        "rounded-md px-1.5 py-0.5 text-[11px] font-bold",
                        CONTROL_LABELS[selected.control_mode].tone,
                      )}
                    >
                      {CONTROL_LABELS[selected.control_mode].text}
                    </span>
                  </div>
                  {selected.listing.title && (
                    <a
                      href={selected.listing.url || undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-[13px] font-bold text-[#5440F6] hover:underline"
                    >
                      {selected.listing.title}
                      {selected.listing.price ? ` · ${selected.listing.price}` : ""}
                      {selected.listing.url && <ExternalLink className="h-3 w-3" />}
                    </a>
                  )}
                </div>
                <div className="flex shrink-0 gap-1.5">
                  {selected.control_mode !== "human" && (
                    <button
                      onClick={() => void changeControl("human", true)}
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-bold text-slate-700 transition hover:bg-slate-50"
                    >
                      Веду сам
                    </button>
                  )}
                  {selected.control_mode !== "ai" && (
                    <button
                      onClick={() => void changeControl("ai")}
                      className="rounded-lg bg-[#EEF2FF] px-3 py-1.5 text-[12px] font-bold text-[#5440F6] transition hover:bg-[#e0e7ff]"
                    >
                      Вернуть ИИ
                    </button>
                  )}
                  {selected.control_mode !== "paused" && (
                    <button
                      onClick={() => void changeControl("paused")}
                      className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-bold text-slate-700 transition hover:bg-slate-50"
                    >
                      <Pause className="h-3 w-3" /> Пауза
                    </button>
                  )}
                </div>
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-4">
                {messages.length === 0 && (
                  <p className="py-8 text-center text-[13px] font-medium text-slate-400">
                    В этом обращении пока нет сохранённых сообщений.
                  </p>
                )}
                {messages.map((message) => {
                  const own = message.direction === "outbound";
                  const system = message.author_type === "system";
                  return (
                    <div
                      key={message.id}
                      className={cn("flex", own ? "justify-end" : "justify-start", system && "justify-center")}
                    >
                      <div
                        className={cn(
                          "max-w-[75%] rounded-2xl px-3.5 py-2.5 text-[14px] font-medium",
                          system
                            ? "bg-slate-100 text-slate-500 text-[12px] font-semibold"
                            : own
                              ? "bg-[#5440F6] text-white"
                              : "bg-slate-100 text-slate-900",
                        )}
                      >
                        {!system && (
                          <div
                            className={cn(
                              "mb-1 flex items-center gap-1 text-[11px] font-bold",
                              own ? "text-white/70" : "text-slate-500",
                            )}
                          >
                            {message.author_type === "agent" ? (
                              <Bot className="h-3 w-3" />
                            ) : (
                              <User className="h-3 w-3" />
                            )}
                            {message.author_type === "agent"
                              ? "ИИ"
                              : message.author_type === "operator"
                                ? message.author_name || "Оператор"
                                : message.author_name || selected.display_name}
                          </div>
                        )}
                        <p className="whitespace-pre-wrap break-words">{message.text}</p>
                        <div
                          className={cn(
                            "mt-1 flex items-center gap-1 text-[11px] font-bold",
                            own ? "text-white/70" : "text-slate-400",
                          )}
                        >
                          {timeLabel(message.occurred_at)}
                          {own && message.delivery_status === "sent" && <CheckCheck className="h-3 w-3" />}
                          {own && message.delivery_status === "pending" && (
                            <span className="flex items-center gap-1">
                              <Clock className="h-3 w-3" /> в очереди
                            </span>
                          )}
                          {own && (message.delivery_status === "failed" || message.delivery_status === "unknown") && (
                            <span className="rounded bg-white/20 px-1">
                              {message.delivery_status === "failed" ? "не доставлено" : "доставка неясна"}
                            </span>
                          )}
                        </div>
                        {message.error_detail && (
                          <p className="mt-1 text-[11px] font-semibold text-rose-200">{message.error_detail}</p>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div ref={bottomRef} />
              </div>

              <div className="border-t border-slate-100 p-4">
                {blockedReason && (
                  <p className="mb-2 rounded-xl bg-amber-50 px-3 py-2 text-[12px] font-bold text-amber-800">
                    {blockedReason} Отправка отклонится, пока это не исправлено.
                  </p>
                )}
                <div className="flex items-end gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
                    }}
                    rows={2}
                    placeholder="Ответ собеседнику. Ctrl+Enter — отправить"
                    className="flex-1 resize-none rounded-xl border border-slate-200 px-3 py-2 text-[14px] font-medium outline-none focus:border-[#5440F6]"
                  />
                  <button
                    onClick={() => void send()}
                    disabled={!draft.trim() || sending || Boolean(blockedReason)}
                    className="flex h-10 items-center gap-2 rounded-xl bg-[#5440F6] px-4 text-[13px] font-bold text-white transition hover:bg-[#4433d6] disabled:opacity-40"
                  >
                    {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    Отправить
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
