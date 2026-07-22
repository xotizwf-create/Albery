import { useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  ExternalLink,
  Bot,
  AlertTriangle,
  Loader2,
  Zap,
  BookOpen,
  Crown,
  Package,
  ArrowUp,
  ArrowDown,
  X,
  Check,
} from "lucide-react";
import {
  fetchAgentDialogs,
  fetchAgents,
  fetchDialogTurns,
  resolveDialogErrors,
  DialogTurn,
} from "../api";
import { AgentConfig, Chat } from "../types";
import { agentSubSegments, setAgentPath } from "../route";
import { cn } from "../../lib/utils";

const DIALOGS_BASE = "/agent-dialogs";

const TAG_STYLES: Record<string, string> = {
  ошибка: "text-rose-600 bg-rose-50 border-rose-100",
  ops: "text-emerald-600 bg-emerald-50 border-emerald-100",
  faq: "text-indigo-600 bg-indigo-50 border-indigo-100",
  admin: "text-amber-600 bg-amber-50 border-amber-100",
};

const FILTERS = [
  { id: "all", label: "Все" },
  { id: "errors", label: "С ошибками" },
  { id: "ops", label: "Все функции (ops)" },
  { id: "faq", label: "База знаний (faq)" },
] as const;

type FilterId = (typeof FILTERS)[number]["id"];

// Подвкладки Telegram: разговор с самим агентом и переписки с людьми — разные потоки.
const TG_SCOPES = [
  { id: "all", label: "Все" },
  { id: "bot_dm", label: "В боте" },
  { id: "lead_chat", label: "С пользователями" },
] as const;

type TgScope = (typeof TG_SCOPES)[number]["id"];

export function DialogsView() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [turns, setTurns] = useState<DialogTurn[]>([]);
  const [channel, setChannel] = useState<"Bitrix" | "Telegram">("Bitrix");
  const [channelNote, setChannelNote] = useState("");
  // Bitrix has two separate streams: ordinary private chats ("chat") and in-task mention
  // threads ("task", dialog_id "task-<id>"). Kept apart so they never mix. Deep links to a
  // task thread (dialog segment "task-…") restore the task tab.
  const [scope, setScope] = useState<"chat" | "task">(
    () => (agentSubSegments(DIALOGS_BASE)[1]?.startsWith("task-") ? "task" : "chat"),
  );
  const [tgScope, setTgScope] = useState<TgScope>("all");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");
  // Контекстное меню по правому клику на диалоге: снять метку «ОШИБКА» после разбора.
  const [errorMenu, setErrorMenu] = useState<{ chat: Chat; x: number; y: number } | null>(null);
  const [resolveDialog, setResolveDialog] = useState<Chat | null>(null);
  const [resolveValue, setResolveValue] = useState("");
  const [resolveError, setResolveError] = useState("");
  const [resolveDone, setResolveDone] = useState("");
  const [resolving, setResolving] = useState(false);
  // Initial selection comes from the URL (/agent-dialogs/<agent>/<dialog>) so a refresh
  // or a shared link restores exactly what was open instead of resetting.
  const [activeAgentId, setActiveAgentId] = useState<string>(
    () => agentSubSegments(DIALOGS_BASE)[0] || "all",
  );
  const [activeChatId, setActiveChatId] = useState<string | null>(() => {
    const seg = agentSubSegments(DIALOGS_BASE);
    return seg[0] && seg[1] ? `${seg[0]}:${seg[1]}` : null;
  });
  const [chatsLoading, setChatsLoading] = useState(true);
  const [turnsLoading, setTurnsLoading] = useState(false);
  const [error, setError] = useState("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const initialScrollRef = useRef(true);

  // Open a dialog at its latest messages, like any messenger; on live updates
  // stick to the bottom only if the reader was already there.
  useEffect(() => {
    if (turnsLoading || turns.length === 0) return;
    if (initialScrollRef.current || atBottomRef.current) {
      messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight });
      initialScrollRef.current = false;
    }
  }, [turnsLoading, turns]);

  const onMessagesScroll = () => {
    const el = messagesRef.current;
    if (el) atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const scrollMessages = (toTop: boolean) => {
    const el = messagesRef.current;
    if (el) el.scrollTo({ top: toTop ? 0 : el.scrollHeight, behavior: "smooth" });
  };

  useEffect(() => {
    fetchAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  // Каналы разделены: у битрикс-ботов channels=["Bitrix"], у телеграмных — ["Telegram"].
  // Без этого в списке Telegram висели бы битрикс-субагенты, у которых там нет переписок.
  const channelAgents = useMemo(
    () => agents.filter((a) => (a.channels || []).includes(channel)),
    [agents, channel],
  );

  // User picked a bot in the left pane: drop the open dialog + stale list so one bot's
  // conversation never lingers under another. URL-driven changes (deep link / back) set
  // the selection directly and intentionally skip this reset.
  const selectAgent = (id: string) => {
    setActiveAgentId(id);
    setActiveChatId(null);
    setChats([]);
  };

  // Switching stream (chats ↔ in-task): drop the open thread + stale list so one stream's
  // conversation never lingers under the other.
  const selectScope = (s: "chat" | "task") => {
    if (s === scope) return;
    setScope(s);
    setActiveChatId(null);
    setChats([]);
  };

  // Telegram has its own two streams: the conversation with the agent itself ("В боте") and
  // the chats with people the agent handles ("Диалоги с пользователями"). Same idea as the
  // Bitrix chats/tasks split — kept apart so they never mix.
  const selectTgScope = (s: TgScope) => {
    if (s === tgScope) return;
    setTgScope(s);
    setActiveChatId(null);
    setChats([]);
  };

  // Reflect the current selection in the URL (replaceState — survives refresh & is
  // shareable, without spamming history): /agent-dialogs[/<agent>[/<dialog>]].
  useEffect(() => {
    if (activeChatId) {
      const sep = activeChatId.indexOf(":");
      setAgentPath(DIALOGS_BASE, [activeChatId.slice(0, sep), activeChatId.slice(sep + 1)]);
    } else {
      setAgentPath(DIALOGS_BASE, [activeAgentId !== "all" ? activeAgentId : null]);
    }
  }, [activeAgentId, activeChatId]);

  // Back/forward: re-read the selection from the URL.
  useEffect(() => {
    const onPop = () => {
      const seg = agentSubSegments(DIALOGS_BASE);
      setActiveAgentId(seg[0] || "all");
      setActiveChatId(seg[0] && seg[1] ? `${seg[0]}:${seg[1]}` : null);
      setScope(seg[1]?.startsWith("task-") ? "task" : "chat");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChatsLoading(true);
      setError("");
      fetchAgentDialogs({
        channel,
        q: query,
        agent: activeAgentId,
        kind: channel === "Bitrix" ? scope : tgScope === "all" ? undefined : tgScope,
      })
        .then(({ chats: loaded, note }) => {
          if (cancelled) return;
          setChats(loaded);
          setChannelNote(note || "");
        })
        .catch((e: Error) => {
          if (!cancelled) setError(e.message);
        })
        .finally(() => {
          if (!cancelled) setChatsLoading(false);
        });
    }, query ? 400 : 0);
    // Живая лента: список переписок сам подтягивает новые вопросы и ответы, иначе владелец
    // видит разговор только после ручного обновления страницы. Тихо — без индикатора загрузки.
    const poll = window.setInterval(() => {
      fetchAgentDialogs({
        channel,
        q: query,
        agent: activeAgentId,
        kind: channel === "Bitrix" ? scope : tgScope === "all" ? undefined : tgScope,
      })
        .then(({ chats: loaded, note }) => {
          if (cancelled) return;
          setChats((prev) => {
            const same =
              prev.length === loaded.length &&
              prev.every((c, i) => c.id === loaded[i].id && c.lastMessage === loaded[i].lastMessage);
            return same ? prev : loaded;
          });
          setChannelNote(note || "");
        })
        .catch(() => {});
    }, 7000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearInterval(poll);
    };
  }, [channel, query, activeAgentId, scope, tgScope]);

  // The agents pane narrows the list to one bot server-side (each bot has its own
  // dialog history — never pooled); here only the tag chips narrow it further.
  const visibleChats = useMemo(
    () =>
      chats.filter((c) => {
        if (filter === "errors" && c.tag !== "ошибка") return false;
        if ((filter === "ops" || filter === "faq") && c.tag !== filter) return false;
        return true;
      }),
    [chats, filter],
  );

  const activeChat =
    visibleChats.find((c) => c.id === activeChatId) || chats.find((c) => c.id === activeChatId) || null;

  // Снятие метки «ОШИБКА»: спрашиваем номер задачи, в которой сбой устранён, чтобы метка
  // снималась по факту работы, а не «просто так» (требование владельца 20.07.2026).
  function openResolveDialog(chat: Chat) {
    setErrorMenu(null);
    setResolveValue("");
    setResolveError("");
    setResolveDone("");
    setResolveDialog(chat);
  }

  async function submitResolve() {
    const chat = resolveDialog;
    if (!chat || resolving) return;
    const text = resolveValue.trim();
    if (!text) {
      setResolveError("Укажите номер задачи или напишите, чем сбой устранён.");
      return;
    }
    const taskId = /^\d+$/.test(text) ? Number(text) : null;
    setResolving(true);
    setResolveError("");
    try {
      const n = await resolveDialogErrors({
        dialogId: chat.dialogId,
        agent: chat.agentId,
        taskId,
        note: taskId ? "" : text,
      });
      if (n > 0) {
        setChats((prev) =>
          prev.map((c) => (c.id === chat.id && c.tag === "ошибка" ? { ...c, tag: "ops" } : c)),
        );
      }
      setResolveDone(
        n ? `Метка снята — разобрано сбоев: ${n}.` : "Неразобранных сбоев в этом диалоге не нашлось.",
      );
      window.setTimeout(() => setResolveDialog(null), 1600);
    } catch (e) {
      setResolveError((e as Error).message || "Не удалось снять метку.");
    } finally {
      setResolving(false);
    }
  }

  useEffect(() => {
    if (!errorMenu) return;
    const close = () => setErrorMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [errorMenu]);

  useEffect(() => {
    if (!activeChatId && visibleChats.length > 0) setActiveChatId(visibleChats[0].id);
  }, [visibleChats, activeChatId]);

  useEffect(() => {
    if (!activeChatId) {
      setTurns([]);
      return;
    }
    // activeChatId is the composite `${agentId}:${dialogId}`; split once so the thread is
    // fetched scoped to this bot (a dialog_id is shared across bots).
    const sep = activeChatId.indexOf(":");
    const chatAgentId = activeChatId.slice(0, sep);
    const chatDialogId = activeChatId.slice(sep + 1);
    let cancelled = false;
    initialScrollRef.current = true;
    atBottomRef.current = true;
    setTurnsLoading(true);
    fetchDialogTurns(chatDialogId, chatAgentId, channel)
      .then((loaded) => {
        if (!cancelled) setTurns(loaded);
      })
      .catch(() => {
        if (!cancelled) setTurns([]);
      })
      .finally(() => {
        if (!cancelled) setTurnsLoading(false);
      });
    // Live updates: silently re-pull the open conversation (state changes only
    // when a new turn actually arrived, so no flicker while reading).
    const timer = window.setInterval(() => {
      fetchDialogTurns(chatDialogId, chatAgentId, channel)
        .then((loaded) => {
          if (cancelled) return;
          setTurns((prev) => {
            const changed =
              loaded.length !== prev.length ||
              (loaded.length > 0 && prev.length > 0 && loaded[loaded.length - 1].id !== prev[prev.length - 1].id);
            return changed ? loaded : prev;
          });
        })
        .catch(() => {});
    }, 12000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeChatId]);

  // Live refresh of the dialog list (new dialogs / previews / error tags).
  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchAgentDialogs({
        channel,
        q: query,
        agent: activeAgentId,
        kind: channel === "Bitrix" ? scope : tgScope === "all" ? undefined : tgScope,
      })
        .then(({ chats: loaded }) => setChats(loaded))
        .catch(() => {});
    }, 20000);
    return () => window.clearInterval(timer);
  }, [channel, query, activeAgentId, scope, tgScope]);

  const agentIcon = (iconType: AgentConfig["iconType"]) =>
    iconType === "zap" ? Zap : iconType === "book" ? BookOpen : iconType === "crown" ? Crown : Package;

  let lastDate = "";

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[520px] bg-white rounded-3xl shadow-sm border border-gray-200/60 overflow-hidden">
      {/* Leftmost Pane - Agents */}
      <div className="w-[280px] border-r border-gray-100 flex flex-col bg-slate-50/50 shrink-0">
        <div className="p-4 border-b border-gray-100 h-16 flex items-center shrink-0">
          <h2 className="font-bold text-gray-900 text-[15px]">Агенты</h2>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          <button
            onClick={() => selectAgent("all")}
            className={cn(
              "w-full flex items-center gap-3 p-3 rounded-2xl transition-all text-left group",
              activeAgentId === "all"
                ? "bg-white shadow-sm border border-gray-200/80"
                : "hover:bg-white/60 border border-transparent",
            )}
          >
            <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm bg-indigo-100 text-indigo-500">
              <Bot className="w-5 h-5" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-gray-900 text-[14px] truncate">Все агенты</div>
              <div className="text-[12px] text-gray-500 font-medium truncate mt-0.5">без фильтра</div>
            </div>
          </button>
          {channelAgents
            .filter((a) => a.isActive)
            .map((agent) => {
              const isActive = agent.id === activeAgentId;
              const Icon = agentIcon(agent.iconType);
              return (
                <button
                  key={agent.id}
                  onClick={() => selectAgent(agent.id)}
                  className={cn(
                    "w-full flex items-center gap-3 p-3 rounded-2xl transition-all text-left group",
                    isActive
                      ? "bg-white shadow-sm border border-gray-200/80"
                      : "hover:bg-white/60 border border-transparent",
                  )}
                >
                  <div
                    className={cn(
                      "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm transition-transform",
                      agent.iconBg,
                      isActive && "scale-105",
                    )}
                  >
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-gray-900 text-[14px] truncate">{agent.name}</div>
                    <div className="text-[12px] text-gray-500 font-medium truncate mt-0.5">{agent.type}</div>
                  </div>
                </button>
              );
            })}
        </div>
      </div>

      {/* Middle Pane - Dialogs List */}
      <div className="w-[320px] border-r border-gray-100 flex flex-col bg-white shrink-0">
        <div className="p-4 border-b border-gray-100 space-y-4 bg-white shrink-0">
          <div className="flex gap-2">
            {(["Bitrix", "Telegram"] as const).map((ch) => (
              <button
                key={ch}
                onClick={() => {
                  setChannel(ch);
                  setActiveChatId(null);
                }}
                className={cn(
                  "flex-1 px-3 py-2 text-sm font-bold rounded-xl transition-colors",
                  channel === ch
                    ? "bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 shadow-sm"
                    : "bg-gray-50 text-gray-500 hover:bg-gray-100 border border-transparent",
                )}
              >
                {ch}
                {channel === ch && (
                  <span className="text-gray-400 ml-1.5 bg-gray-100 px-1.5 py-0.5 rounded-md">{chats.length}</span>
                )}
              </button>
            ))}
          </div>
          {channel === "Telegram" && (
            <div className="flex gap-1 p-1 bg-gray-100/70 rounded-xl">
              {TG_SCOPES.map((s) => (
                <button
                  key={s.id}
                  onClick={() => selectTgScope(s.id)}
                  className={cn(
                    "flex-1 px-2 py-1.5 text-[12.5px] font-bold rounded-lg transition-colors flex items-center justify-center gap-1.5",
                    tgScope === s.id ? "bg-white text-sky-700 shadow-sm" : "text-gray-500 hover:text-gray-700",
                  )}
                >
                  {s.label}
                  {tgScope === s.id && (
                    <span className="text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-md text-[11px]">
                      {chats.length}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
          {channel === "Bitrix" && (
            <div className="flex gap-1 p-1 bg-gray-100/70 rounded-xl">
              {(
                [
                  { id: "chat", label: "Чаты" },
                  { id: "task", label: "В задачах" },
                ] as const
              ).map((s) => (
                <button
                  key={s.id}
                  onClick={() => selectScope(s.id)}
                  className={cn(
                    "flex-1 px-3 py-1.5 text-[12.5px] font-bold rounded-lg transition-colors flex items-center justify-center gap-1.5",
                    scope === s.id ? "bg-white text-indigo-700 shadow-sm" : "text-gray-500 hover:text-gray-700",
                  )}
                >
                  {s.label}
                  {scope === s.id && (
                    <span className="text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-md text-[11px]">{chats.length}</span>
                  )}
                </button>
              ))}
            </div>
          )}
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Поиск по переписке..."
              className="w-full pl-10 pr-4 py-2 bg-gray-50 border-none rounded-xl text-[13.5px] font-medium focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
            />
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 [&::-webkit-scrollbar]:hidden">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className={cn(
                  "whitespace-nowrap px-3 py-1.5 rounded-lg text-xs font-bold transition-colors",
                  filter === f.id ? "bg-indigo-50 text-indigo-700" : "bg-gray-50 text-gray-600 hover:bg-gray-100",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          {chatsLoading && (
            <div className="flex items-center justify-center gap-2 py-10 text-gray-400 text-[13px] font-bold">
              <Loader2 className="w-4 h-4 animate-spin" /> Загрузка диалогов…
            </div>
          )}
          {!chatsLoading && error && (
            <div className="flex items-center gap-2 m-4 p-3 rounded-xl bg-rose-50 border border-rose-100 text-rose-600 text-[12.5px] font-bold">
              <AlertTriangle className="w-4 h-4 shrink-0" /> {error}
            </div>
          )}
          {!chatsLoading && !error && visibleChats.length === 0 && (
            <div className="py-10 px-6 text-center text-gray-400 text-[13px] font-medium">
              {channelNote ||
                (query
                  ? `По запросу «${query}» ничего не найдено`
                  : scope === "task"
                    ? "Обращений к агентам в задачах пока нет"
                    : "Диалогов пока нет")}
            </div>
          )}
          {!chatsLoading &&
            visibleChats.map((chat) => (
              <button
                key={chat.id}
                onClick={() => setActiveChatId(chat.id)}
                onContextMenu={(e) => {
                  if (chat.tag !== "ошибка") return;
                  e.preventDefault();
                  setErrorMenu({ chat, x: e.clientX, y: e.clientY });
                }}
                title={chat.tag === "ошибка" ? "Правый клик — снять метку «ОШИБКА» после разбора" : undefined}
                className={cn(
                  "w-full p-4 flex items-start gap-3 border-b border-gray-50 transition-all text-left relative",
                  activeChatId === chat.id ? "bg-indigo-50/30" : "hover:bg-gray-50",
                )}
              >
                {activeChatId === chat.id && (
                  <div className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-600 rounded-r-full" />
                )}
                <div
                  className={cn(
                    "w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[13px] shrink-0 shadow-sm",
                    chat.avatarColor,
                  )}
                >
                  {chat.avatarInitials}
                </div>
                <div className="flex-1 min-w-0 pt-0.5">
                  <div className="flex justify-between items-center mb-1">
                    <span className="font-bold text-[14px] text-gray-900 truncate pr-2">{chat.userName}</span>
                    <span className="text-[11px] font-bold text-gray-400 shrink-0">{chat.time}</span>
                  </div>
                  <div className="text-[13px] text-gray-500 font-medium mb-2 truncate">{chat.lastMessage}</div>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "text-[10px] font-bold px-1.5 py-0.5 rounded-md border uppercase tracking-wider",
                        TAG_STYLES[chat.tag] || TAG_STYLES.faq,
                      )}
                    >
                      {chat.tag}
                    </span>
                  </div>
                </div>
              </button>
            ))}
        </div>
      </div>

      {/* Right Chat Area */}
      <div className="flex-1 flex flex-col bg-white min-w-0 max-w-full overflow-hidden">
        {!activeChat ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-[14px] font-medium">
            Выберите диалог слева
          </div>
        ) : (
          <>
            <div className="h-16 border-b border-gray-100 flex items-center justify-between px-6 shrink-0 bg-white">
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    "w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[13px] shadow-sm",
                    activeChat.avatarColor,
                  )}
                >
                  {activeChat.avatarInitials}
                </div>
                <div>
                  <h2 className="font-bold text-[15px] text-gray-900 leading-tight">{activeChat.userName}</h2>
                  <div className="text-[12px] text-gray-500 font-medium flex items-center gap-1 mt-0.5">
                    {activeChat.userRole} •
                    <Bot className="w-3.5 h-3.5 text-indigo-500 ml-1" />
                    <span className="text-indigo-600 font-bold uppercase">
                      {activeChat.tag === "ошибка" ? "были ошибки" : activeChat.tag}
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 text-[13px] font-bold text-gray-400 bg-white border border-gray-100 rounded-xl">
                {activeChat.taskId ? `в задаче #${activeChat.taskId}` : `диалог ${activeChat.dialogId}`}
                <ExternalLink className="w-4 h-4" />
              </div>
            </div>

            <div className="relative flex-1 min-h-0 min-w-0 overflow-hidden bg-slate-50/50">
              <div
                ref={messagesRef}
                onScroll={onMessagesScroll}
                className="h-full w-full overflow-y-auto overflow-x-hidden p-6 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full"
              >
                <div className="w-full max-w-[860px] mx-auto space-y-6">
                  {turnsLoading && (
                    <div className="flex items-center justify-center gap-2 py-10 text-gray-400 text-[13px] font-bold">
                      <Loader2 className="w-4 h-4 animate-spin" /> Загрузка переписки…
                    </div>
                  )}
                  {!turnsLoading && turns.length === 0 && (
                    <div className="py-10 text-center text-gray-400 text-[13px] font-medium">Сообщений нет</div>
                  )}
                  {!turnsLoading &&
                    turns.map((turn) => {
                      const showDate = turn.date !== lastDate;
                      lastDate = turn.date;
                      return (
                        <div key={turn.id} className="space-y-6 w-full max-w-full min-w-0">
                          {showDate && (
                            <div className="flex justify-center">
                              <span className="text-[11px] font-bold text-gray-400 bg-white border border-gray-200 px-3 py-1 rounded-lg shadow-sm">
                                {turn.date}
                              </span>
                            </div>
                          )}
                          {turn.question && (
                            <div className="flex flex-col items-start w-full">
                              <div className="w-fit max-w-[85%] min-w-0 px-5 py-3.5 rounded-2xl text-[14px] font-medium leading-relaxed shadow-sm bg-white text-gray-800 rounded-tl-sm border border-gray-100 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                                {turn.question}
                              </div>
                              <span className="text-[10.5px] font-bold text-gray-400 mt-1.5 px-1">{turn.time}</span>
                            </div>
                          )}
                          {turn.status !== "ok" ? (
                            <div className="flex flex-col items-end w-full">
                              <div className="w-fit max-w-[85%] min-w-0 p-5 rounded-2xl shadow-sm border bg-rose-500 border-rose-400 text-white">
                                <div className="font-bold text-[15px] mb-2">Ошибка обработки</div>
                                {(turn.error || turn.answer) && (
                                  <div className="text-[13.5px] text-rose-50 font-medium leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                                    {turn.error || turn.answer}
                                  </div>
                                )}
                                <div className="mt-3 text-[11px] font-bold text-rose-100 uppercase tracking-wider">
                                  {turn.time}
                                  {turn.latency_ms ? ` • ⏱ ${Math.round(turn.latency_ms / 1000)} сек` : ""} • {turn.tier}
                                </div>
                              </div>
                            </div>
                          ) : (
                            turn.answer && (
                              <div className="flex flex-col items-end w-full">
                                <div className="w-fit max-w-[85%] min-w-0 px-5 py-3.5 rounded-2xl text-[14px] font-medium leading-relaxed shadow-sm bg-indigo-600 text-white rounded-tr-sm whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                                  {turn.answer}
                                </div>
                                <span className="text-[10.5px] font-bold text-gray-400 mt-1.5 px-1">
                                  {turn.time}
                                  {turn.latency_ms ? ` • ⏱ ${Math.round(turn.latency_ms / 1000)} сек` : ""}
                                  {turn.session_name ? ` • ${turn.session_name}` : ""}
                                </span>
                              </div>
                            )
                          )}
                        </div>
                      );
                    })}
                </div>
              </div>

              {turns.length > 0 && !turnsLoading && (
                <div className="absolute right-4 bottom-4 flex flex-col gap-2 z-10">
                  <button
                    onClick={() => scrollMessages(true)}
                    title="К началу переписки"
                    className="w-10 h-10 rounded-full bg-white border border-gray-200 shadow-md flex items-center justify-center text-gray-500 hover:text-indigo-600 hover:border-indigo-200 hover:shadow-lg active:scale-95 transition-all"
                  >
                    <ArrowUp className="w-5 h-5" strokeWidth={2.5} />
                  </button>
                  <button
                    onClick={() => scrollMessages(false)}
                    title="К последним сообщениям"
                    className="w-10 h-10 rounded-full bg-white border border-gray-200 shadow-md flex items-center justify-center text-gray-500 hover:text-indigo-600 hover:border-indigo-200 hover:shadow-lg active:scale-95 transition-all"
                  >
                    <ArrowDown className="w-5 h-5" strokeWidth={2.5} />
                  </button>
                </div>
              )}
            </div>

            <div className="p-3.5 text-center border-t border-gray-100 bg-white text-[12px] text-gray-400 font-bold uppercase tracking-wider">
              👁 Режим просмотра — вы видите переписку сотрудника с агентом
            </div>
          </>
        )}
      </div>

      {errorMenu && (
        <div
          className="fixed z-50 bg-white border border-gray-200 rounded-xl shadow-xl py-1.5 min-w-[260px]"
          style={{ left: errorMenu.x, top: errorMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-[11px] font-bold text-gray-400 uppercase tracking-wider">
            {errorMenu.chat.userName}
          </div>
          <button
            onClick={() => openResolveDialog(errorMenu.chat)}
            className="w-full text-left px-3 py-2 text-[13px] font-semibold text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 flex items-center gap-2"
          >
            <AlertTriangle className="w-4 h-4" />
            Ошибка устранена — снять метку
          </button>
          <div className="px-3 pb-1.5 pt-0.5 text-[11px] text-gray-400 leading-snug">
            Спросим номер задачи, в которой сбой устранён
          </div>
        </div>
      )}

      {resolveDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
            onClick={() => !resolving && setResolveDialog(null)}
          />
          <div className="relative bg-white rounded-3xl shadow-2xl border border-gray-100 w-full max-w-md p-7">
            <div className="flex items-start justify-between mb-5">
              <div>
                <h2 className="text-lg font-bold text-gray-900 leading-tight">Ошибка устранена</h2>
                <p className="text-[13px] text-gray-500 font-medium mt-1">
                  Диалог «{resolveDialog.userName}»
                </p>
              </div>
              <button
                onClick={() => !resolving && setResolveDialog(null)}
                className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {resolveDone ? (
              <div className="flex items-center gap-2.5 px-4 py-3.5 rounded-2xl bg-emerald-50 border border-emerald-100 text-emerald-700 text-[13px] font-bold">
                <Check className="w-4.5 h-4.5 shrink-0" />
                {resolveDone}
              </div>
            ) : (
              <>
                <label className="block text-sm font-bold text-gray-900 mb-2">
                  Номер задачи Битрикса
                </label>
                <input
                  autoFocus
                  type="text"
                  value={resolveValue}
                  onChange={(e) => {
                    setResolveValue(e.target.value);
                    setResolveError("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitResolve();
                    if (e.key === "Escape" && !resolving) setResolveDialog(null);
                  }}
                  placeholder="Например: 1820"
                  className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
                />
                <p className="text-[12px] text-gray-400 font-medium mt-2 leading-snug">
                  В задаче должно быть видно, что сбой действительно устранён. Если задачи нет —
                  напишите словами, чем устранён.
                </p>
                {resolveError && (
                  <div className="mt-3 px-3.5 py-2.5 rounded-xl bg-rose-50 border border-rose-100 text-rose-600 text-[12px] font-bold">
                    {resolveError}
                  </div>
                )}
                <div className="flex gap-2.5 mt-6">
                  <button
                    onClick={() => void submitResolve()}
                    disabled={resolving}
                    className="flex-1 px-4 py-3 rounded-xl bg-indigo-600 text-white text-[13px] font-bold hover:bg-indigo-700 active:scale-[0.99] disabled:opacity-60 transition-all flex items-center justify-center gap-2 shadow-sm"
                  >
                    {resolving && <Loader2 className="w-4 h-4 animate-spin" />}
                    Снять метку
                  </button>
                  <button
                    onClick={() => setResolveDialog(null)}
                    disabled={resolving}
                    className="px-4 py-3 rounded-xl bg-gray-50 border border-gray-200/80 text-gray-600 text-[13px] font-bold hover:bg-gray-100 disabled:opacity-60 transition-all"
                  >
                    Отмена
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
