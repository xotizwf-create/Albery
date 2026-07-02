import { useEffect, useMemo, useState } from "react";
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
} from "lucide-react";
import { fetchAgentDialogs, fetchAgents, fetchDialogTurns, DialogTurn } from "../api";
import { AgentConfig, Chat } from "../types";
import { cn } from "../../lib/utils";

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

export function DialogsView() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [turns, setTurns] = useState<DialogTurn[]>([]);
  const [channel, setChannel] = useState<"Bitrix" | "Telegram">("Bitrix");
  const [channelNote, setChannelNote] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");
  const [activeAgentId, setActiveAgentId] = useState<string>("all");
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [chatsLoading, setChatsLoading] = useState(true);
  const [turnsLoading, setTurnsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChatsLoading(true);
      setError("");
      fetchAgentDialogs({ channel, q: query })
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
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [channel, query]);

  // A single live agent today, so the agents pane is a selector for the future
  // multi-agent case; only the tag chips actually narrow the list.
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

  useEffect(() => {
    if (!activeChatId && visibleChats.length > 0) setActiveChatId(visibleChats[0].id);
  }, [visibleChats, activeChatId]);

  useEffect(() => {
    if (!activeChatId) {
      setTurns([]);
      return;
    }
    let cancelled = false;
    setTurnsLoading(true);
    fetchDialogTurns(activeChatId)
      .then((loaded) => {
        if (!cancelled) setTurns(loaded);
      })
      .catch(() => {
        if (!cancelled) setTurns([]);
      })
      .finally(() => {
        if (!cancelled) setTurnsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeChatId]);

  const agentIcon = (iconType: AgentConfig["iconType"]) =>
    iconType === "zap" ? Zap : iconType === "book" ? BookOpen : iconType === "crown" ? Crown : Package;

  let lastDate = "";

  return (
    <div className="flex h-[calc(100vh-6rem)] bg-white rounded-3xl shadow-sm border border-gray-200/60 overflow-hidden">
      {/* Leftmost Pane - Agents */}
      <div className="w-[280px] border-r border-gray-100 flex flex-col bg-slate-50/50 shrink-0">
        <div className="p-4 border-b border-gray-100 h-16 flex items-center shrink-0">
          <h2 className="font-bold text-gray-900 text-[15px]">Агенты</h2>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          <button
            onClick={() => setActiveAgentId("all")}
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
          {agents
            .filter((a) => a.isActive)
            .map((agent) => {
              const isActive = agent.id === activeAgentId;
              const Icon = agentIcon(agent.iconType);
              return (
                <button
                  key={agent.id}
                  onClick={() => setActiveAgentId(agent.id)}
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
              {channelNote || (query ? `По запросу «${query}» ничего не найдено` : "Диалогов пока нет")}
            </div>
          )}
          {!chatsLoading &&
            visibleChats.map((chat) => (
              <button
                key={chat.id}
                onClick={() => setActiveChatId(chat.id)}
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
      <div className="flex-1 flex flex-col bg-white min-w-0">
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
                {activeChat.id}
                <ExternalLink className="w-4 h-4" />
              </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-6 bg-slate-50/50 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
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
                    <div key={turn.id} className="space-y-6">
                      {showDate && (
                        <div className="flex justify-center">
                          <span className="text-[11px] font-bold text-gray-400 bg-white border border-gray-200 px-3 py-1 rounded-lg shadow-sm">
                            {turn.date}
                          </span>
                        </div>
                      )}
                      {turn.question && (
                        <div className="flex flex-col max-w-[85%] items-start">
                          <div className="px-5 py-3.5 rounded-2xl text-[14px] font-medium leading-relaxed shadow-sm bg-white text-gray-800 rounded-tl-sm border border-gray-100 whitespace-pre-wrap">
                            {turn.question}
                          </div>
                          <span className="text-[10.5px] font-bold text-gray-400 mt-1.5 px-1">{turn.time}</span>
                        </div>
                      )}
                      {turn.status !== "ok" ? (
                        <div className="flex flex-col max-w-[85%] items-end ml-auto">
                          <div className="p-5 rounded-2xl w-full max-w-[420px] shadow-sm border bg-rose-500 border-rose-400 text-white">
                            <div className="font-bold text-[15px] mb-2">Ошибка обработки</div>
                            {(turn.error || turn.answer) && (
                              <div className="text-[13.5px] text-rose-50 font-medium leading-relaxed whitespace-pre-wrap">
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
                          <div className="flex flex-col max-w-[85%] items-end ml-auto">
                            <div className="px-5 py-3.5 rounded-2xl text-[14px] font-medium leading-relaxed shadow-sm bg-indigo-600 text-white rounded-tr-sm whitespace-pre-wrap">
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

            <div className="p-3.5 text-center border-t border-gray-100 bg-white text-[12px] text-gray-400 font-bold uppercase tracking-wider">
              👁 Режим просмотра — вы видите переписку сотрудника с агентом
            </div>
          </>
        )}
      </div>
    </div>
  );
}
