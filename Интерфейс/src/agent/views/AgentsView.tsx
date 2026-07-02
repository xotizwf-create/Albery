import React, { useEffect, useState } from "react";
import {
  Plus,
  BookOpen,
  Crown,
  Package,
  Check,
  MessageSquare,
  Send,
  X,
  Network,
  Search,
  User,
} from "lucide-react";
import { mockKnowledge } from "../data";
import {
  AccessMember,
  AccessTier,
  BitrixUser,
  TIER_LABELS,
  deleteAccess,
  fetchAccessMembers,
  fetchAgents,
  fetchBitrixUsers,
  upsertAccess,
} from "../api";
import { AgentConfig } from "../types";
import { cn } from "../../lib/utils";

const toolsList = [
  {
    id: "bitrix",
    icon: "📝",
    title: "Задачи Bitrix",
    desc: "Поиск, постановка и комментирование задач",
    scopes: ["read", "write"],
  },
  {
    id: "knowledge",
    icon: "📚",
    title: "База знаний",
    desc: "Поиск по регламентам, документам и оргструктуре",
    scopes: ["search"],
  },
  {
    id: "zoom",
    icon: "🎥",
    title: "Zoom-созвоны",
    desc: "Транскрипты, списки участников и итоги",
    scopes: ["read"],
  },
  {
    id: "docs",
    icon: "📊",
    title: "Google-документы",
    desc: "Чтение и запись таблиц, создание отчётов",
    scopes: ["read", "write"],
  },
  {
    id: "telegram",
    icon: "📱",
    title: "Telegram API",
    desc: "Отправка уведомлений и файлов в чаты",
    scopes: ["write"],
  },
  {
    id: "analytics",
    icon: "📈",
    title: "Сводная аналитика",
    desc: "Доступ к дашбордам и метрикам",
    scopes: ["read"],
  },
];

const AgentEditor: React.FC<{ agent: any; onToggleActive: () => void }> = ({
  agent,
  onToggleActive,
}) => {
  const [activeChannels, setActiveChannels] = useState<string[]>(
    agent.channels,
  );
  const [activeTools, setActiveTools] = useState<Record<string, boolean>>({
    bitrix: true,
    knowledge: true,
    zoom: false,
    docs: false,
    telegram: false,
    analytics: false,
  });

  const [selectedKnowledge, setSelectedKnowledge] = useState<string[]>([
    "Постановка задач",
    "Стиль общения",
  ]);

  const [searchMcp, setSearchMcp] = useState("");
  const [searchKnowledge, setSearchKnowledge] = useState("");

  // Team access = the real agent_access table (same CRUD the Настройки tab uses).
  const [members, setMembers] = useState<AccessMember[]>([]);
  const [bitrixUsers, setBitrixUsers] = useState<BitrixUser[]>([]);
  const [accessError, setAccessError] = useState("");
  const [accessBusy, setAccessBusy] = useState(false);
  const [activeDropdown, setActiveDropdown] = useState<number | null>(null);

  const [showUserSearch, setShowUserSearch] = useState(false);
  const [userSearchQuery, setUserSearchQuery] = useState("");

  useEffect(() => {
    fetchAccessMembers()
      .then(setMembers)
      .catch((e: Error) => setAccessError(e.message));
    fetchBitrixUsers()
      .then(setBitrixUsers)
      .catch(() => {});
  }, []);

  const memberName = (m: AccessMember) =>
    bitrixUsers.find((u) => u.id === m.bitrix_user_id)?.name || m.display_name;

  const runAccessOp = async (op: () => Promise<void>) => {
    setAccessBusy(true);
    setAccessError("");
    try {
      await op();
      setMembers(await fetchAccessMembers());
    } catch (e) {
      setAccessError((e as Error).message);
    } finally {
      setAccessBusy(false);
    }
  };

  const changeTier = (m: AccessMember, tier: AccessTier) => {
    setActiveDropdown(null);
    void runAccessOp(() => upsertAccess(m.bitrix_user_id, tier, memberName(m)));
  };

  const removeMember = (m: AccessMember) => {
    void runAccessOp(() => deleteAccess(m.bitrix_user_id));
  };

  const addMember = (u: BitrixUser) => {
    setShowUserSearch(false);
    setUserSearchQuery("");
    // Least privilege by default: new members start at FAQ; raise via the dropdown.
    void runAccessOp(() => upsertAccess(u.id, "faq", u.name));
  };

  const memberIds = new Set(members.map((m) => m.bitrix_user_id));
  const unselectedUsers = bitrixUsers.filter(
    (u) => !memberIds.has(u.id) && u.name.toLowerCase().includes(userSearchQuery.toLowerCase()),
  );

  const filteredTools = toolsList.filter(
    (t) =>
      t.title.toLowerCase().includes(searchMcp.toLowerCase()) ||
      t.desc.toLowerCase().includes(searchMcp.toLowerCase()),
  );

  const filteredKnowledge = mockKnowledge.filter(
    (k) =>
      k.title.toLowerCase().includes(searchKnowledge.toLowerCase()) ||
      k.description.toLowerCase().includes(searchKnowledge.toLowerCase()),
  );

  const toggleChannel = (channel: string) => {
    setActiveChannels((prev) =>
      prev.includes(channel)
        ? prev.filter((c) => c !== channel)
        : [...prev, channel],
    );
  };

  const toggleTool = (tool: string) => {
    setActiveTools((prev) => ({ ...prev, [tool]: !prev[tool] }));
  };

  const toggleKnowledge = (skill: string) => {
    setSelectedKnowledge((prev) =>
      prev.includes(skill) ? prev.filter((s) => s !== skill) : [...prev, skill],
    );
  };

  return (
    <div className="bg-white rounded-3xl shadow-sm border border-gray-200/60 flex flex-col">
      {/* Editor Header */}
      <div className="bg-slate-50/50 p-6 md:px-8 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div
            className={cn(
              "w-12 h-12 rounded-xl flex items-center justify-center shadow-sm",
              agent.iconBg,
            )}
          >
            <Package className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-3 mb-0.5">
              <h2 className="text-xl font-bold text-gray-900 leading-none">
                {agent.name}
              </h2>
              <button
                onClick={() => {
                  agent.isActive = !agent.isActive;
                  onToggleActive();
                }}
                className={cn(
                  "px-2 py-0.5 border text-[10px] font-bold rounded-md uppercase tracking-wider transition-colors",
                  agent.isActive
                    ? "bg-emerald-50 border-emerald-100 text-emerald-600 hover:bg-emerald-100"
                    : "bg-gray-100 border-gray-200 text-gray-500 hover:bg-gray-200",
                )}
              >
                {agent.isActive ? "включён" : "выключен"}
              </button>
            </div>
            <p className="text-sm text-gray-500 font-medium mt-1">
              Режим конструктора: изменения применяются моментально
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button className="px-5 py-2.5 bg-white text-gray-700 border border-gray-200 rounded-xl text-sm font-bold hover:bg-gray-50 transition-all shadow-sm">
            В песочницу
          </button>
          <button className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all">
            Сохранить
          </button>
        </div>
      </div>

      <div className="p-6 md:p-8 flex flex-col xl:flex-row gap-8">
        {/* Left Column - General Settings */}
        <div className="flex-1 space-y-8 min-w-0">
          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">
              Имя агента
            </label>
            <input
              type="text"
              defaultValue={agent.name}
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">
              Каналы связи
            </label>
            <div className="flex p-1 bg-gray-50/80 border border-gray-200/60 rounded-2xl shadow-sm">
              <button
                onClick={() => toggleChannel("Bitrix")}
                className={cn(
                  "flex items-center justify-center gap-2 py-2.5 rounded-xl transition-all font-bold text-[13.5px] flex-1",
                  activeChannels.includes("Bitrix")
                    ? "bg-white text-gray-900 shadow-sm border border-gray-100"
                    : "text-gray-500 hover:text-gray-700 hover:bg-gray-100/50",
                )}
              >
                <MessageSquare className="w-4 h-4 text-blue-500" />
                Bitrix24
              </button>
              <button
                onClick={() => toggleChannel("Telegram")}
                className={cn(
                  "flex items-center justify-center gap-2 py-2.5 rounded-xl transition-all font-bold text-[13.5px] flex-1",
                  activeChannels.includes("Telegram")
                    ? "bg-white text-gray-900 shadow-sm border border-gray-100"
                    : "text-gray-500 hover:text-gray-700 hover:bg-gray-100/50",
                )}
              >
                <Send className="w-4 h-4 text-sky-500" />
                Telegram
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">
              Роль и системный промпт
            </label>
            <textarea
              rows={4}
              defaultValue="Ты — помощник склада. Отвечаешь только по остаткам, поставкам и задачам склада. Задачи ставишь только на сотрудников склада. Финансовые вопросы переадресуй Александру."
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-[13.5px] focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all resize-none font-medium leading-relaxed shadow-sm"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-bold text-gray-900">
                Команда и доступы
              </label>
              <span className="text-xs font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-md">
                {members.length} сотрудников
              </span>
            </div>
            <div
              className={cn(
                "p-2.5 bg-gray-50/80 border border-gray-200/80 rounded-2xl flex flex-wrap gap-2 items-center shadow-sm transition-opacity",
                accessBusy && "opacity-60 pointer-events-none",
              )}
            >
              {members.map((m) => {
                const name = memberName(m);
                return (
                  <div
                    key={m.bitrix_user_id}
                    className="pl-1.5 pr-2 py-1.5 bg-white border border-gray-200/80 rounded-xl flex items-center gap-2 shadow-sm relative"
                  >
                    <div className="w-7 h-7 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-700 flex items-center justify-center text-[10px] font-bold shrink-0">
                      {name
                        .split(/\s+/)
                        .filter(Boolean)
                        .slice(0, 2)
                        .map((n) => n[0])
                        .join("")}
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[13px] font-bold text-gray-800 leading-tight mb-0.5">
                        {name}
                      </span>
                      <button
                        onClick={() =>
                          setActiveDropdown(activeDropdown === m.bitrix_user_id ? null : m.bitrix_user_id)
                        }
                        className="text-[10px] font-bold text-gray-400 bg-transparent border-none outline-none p-0 h-auto leading-tight cursor-pointer hover:text-indigo-600 transition-colors text-left flex items-center gap-1"
                      >
                        {TIER_LABELS[m.tier]}
                        <span className="text-[8px]">▼</span>
                      </button>
                      {activeDropdown === m.bitrix_user_id && (
                        <>
                          <div
                            className="fixed inset-0 z-10"
                            onClick={() => setActiveDropdown(null)}
                          />
                          <div className="absolute top-full left-0 mt-1 w-36 bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-20">
                            {(Object.keys(TIER_LABELS) as AccessTier[]).map((tier) => (
                              <button
                                key={tier}
                                onClick={() => changeTier(m, tier)}
                                className={cn(
                                  "w-full text-left px-3 py-1.5 text-[11px] font-bold transition-colors",
                                  tier === m.tier
                                    ? "text-indigo-600 bg-indigo-50/50"
                                    : "text-gray-600 hover:bg-gray-50",
                                )}
                              >
                                {TIER_LABELS[tier]}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                    <button
                      onClick={() => removeMember(m)}
                      title="Забрать доступ"
                      className="text-gray-400 hover:text-rose-500 transition-colors bg-gray-50 hover:bg-rose-50 rounded-md p-1 ml-1 shrink-0"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                );
              })}
              <div className="flex-1 min-w-[140px] flex items-center gap-2 px-2 relative">
                <User className="w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Добавить..."
                  value={userSearchQuery}
                  onChange={(e) => {
                    setUserSearchQuery(e.target.value);
                    setShowUserSearch(true);
                  }}
                  onFocus={() => setShowUserSearch(true)}
                  className="w-full bg-transparent border-none outline-none text-[13.5px] font-bold text-gray-700 placeholder:text-gray-400 placeholder:font-medium py-1.5"
                />

                {showUserSearch && (
                  <>
                    <div
                      className="fixed inset-0 z-10"
                      onClick={() => setShowUserSearch(false)}
                    />
                    <div className="absolute top-full left-0 mt-2 w-full min-w-[200px] bg-white rounded-2xl shadow-xl border border-gray-100 py-1.5 z-20 max-h-48 overflow-y-auto">
                      {unselectedUsers.length > 0 ? (
                        unselectedUsers.map((u) => (
                          <button
                            key={u.id}
                            onClick={() => addMember(u)}
                            className="w-full text-left px-3 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
                          >
                            <div className="w-6 h-6 rounded-md bg-gray-100 text-gray-600 flex items-center justify-center text-[9px] font-bold">
                              {u.name
                                .split(/\s+/)
                                .filter(Boolean)
                                .slice(0, 2)
                                .map((n) => n[0])
                                .join("")}
                            </div>
                            <div className="flex flex-col min-w-0">
                              <span className="text-[13px] font-bold text-gray-700 truncate">
                                {u.name}
                              </span>
                              {u.position && (
                                <span className="text-[11px] font-medium text-gray-400 truncate">
                                  {u.position}
                                </span>
                              )}
                            </div>
                          </button>
                        ))
                      ) : (
                        <div className="px-3 py-4 text-center text-[12px] font-medium text-gray-400">
                          {bitrixUsers.length === 0 ? "Загрузка сотрудников…" : "Сотрудники не найдены"}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>
            {accessError && (
              <div className="mt-2 text-[12px] font-bold text-rose-500">{accessError}</div>
            )}
          </div>
        </div>

        {/* Right Column - Constructor (Knowledge & Tools) */}
        <div className="flex-1 space-y-6 min-w-0">
          {/* Integrations & Tools (MCP) */}
          <div className="bg-slate-50/50 p-6 rounded-3xl border border-gray-100 flex flex-col h-[380px]">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2 text-gray-900 font-bold text-[15px]">
                <Network className="w-5 h-5 text-emerald-500" />
                Инструменты (MCP)
              </div>
              <span className="text-[11px] font-bold text-emerald-600 bg-emerald-50 px-2.5 py-0.5 rounded-md border border-emerald-100">
                {Object.values(activeTools).filter(Boolean).length} активно
              </span>
            </div>

            <div className="relative mb-4 shrink-0">
              <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Умный поиск инструментов..."
                value={searchMcp}
                onChange={(e) => setSearchMcp(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13.5px] font-medium focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none transition-all shadow-sm"
              />
            </div>

            <div className="overflow-y-auto pr-2 space-y-2 flex-1 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
              {filteredTools.map((tool) => (
                <div
                  key={tool.id}
                  className={cn(
                    "flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl transition-all cursor-pointer group",
                    activeTools[tool.id]
                      ? "bg-white border border-gray-100 shadow-sm"
                      : "bg-transparent border border-transparent hover:bg-white/60",
                  )}
                  onClick={() => toggleTool(tool.id)}
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    {tool.icon}
                  </div>
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="flex items-center gap-2.5 mb-0.5">
                      <span className="font-bold text-gray-900 text-[14px] truncate">
                        {tool.title}
                      </span>
                      <div className="flex gap-1.5 hidden sm:flex">
                        {tool.scopes.map((s) => (
                          <span
                            key={s}
                            className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md bg-gray-50 border border-gray-100 text-gray-500 shadow-sm"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">
                      {tool.desc}
                    </p>
                  </div>
                  <div
                    className={cn(
                      "w-10 h-6 rounded-full flex items-center px-0.5 transition-colors shrink-0",
                      activeTools[tool.id]
                        ? "bg-indigo-500"
                        : "bg-gray-200 group-hover:bg-gray-300",
                    )}
                  >
                    <div
                      className={cn(
                        "w-5 h-5 rounded-full bg-white transition-transform shadow-sm",
                        activeTools[tool.id] && "translate-x-4",
                      )}
                    />
                  </div>
                </div>
              ))}
              {filteredTools.length === 0 && (
                <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">
                  Ничего не найдено
                </div>
              )}
            </div>
          </div>

          {/* Knowledge & Skills */}
          <div className="bg-slate-50/50 p-6 rounded-3xl border border-gray-100 flex flex-col h-[380px]">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2 text-gray-900 font-bold text-[15px]">
                <BookOpen className="w-5 h-5 text-indigo-500" />
                Инструкции и Скиллы
              </div>
              <span className="text-[11px] font-bold text-indigo-600 bg-indigo-50 px-2.5 py-0.5 rounded-md border border-indigo-100">
                {selectedKnowledge.length} выбрано
              </span>
            </div>

            <div className="relative mb-4 shrink-0">
              <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Умный поиск по базе знаний..."
                value={searchKnowledge}
                onChange={(e) => setSearchKnowledge(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13.5px] font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
              />
            </div>

            <div className="overflow-y-auto pr-2 space-y-2 flex-1 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
              {filteredKnowledge.map((k) => {
                const isSelected = selectedKnowledge.includes(k.title);
                return (
                  <div
                    key={k.id}
                    className={cn(
                      "flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl transition-all cursor-pointer group",
                      isSelected
                        ? "bg-white border border-gray-100 shadow-sm"
                        : "bg-transparent border border-transparent hover:bg-white/60",
                    )}
                    onClick={() => toggleKnowledge(k.title)}
                  >
                    <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                      {k.type === "Инструкция" && !k.isLocked
                        ? "💬"
                        : k.type === "Скилл"
                          ? "🔧"
                          : k.type === "Регламент"
                            ? "📋"
                            : "🔒"}
                    </div>
                    <div className="flex-1 min-w-0 pr-4">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="font-bold text-gray-900 text-[14px] truncate">
                          {k.title}
                        </span>
                      </div>
                      <p className="text-[12.5px] font-medium text-gray-500 truncate">
                        {k.description}
                      </p>
                    </div>
                    <div
                      className={cn(
                        "w-6 h-6 rounded-md border flex items-center justify-center shrink-0 transition-colors shadow-sm",
                        isSelected
                          ? "bg-indigo-500 border-indigo-500 text-white"
                          : "bg-white border-gray-200 text-transparent group-hover:border-indigo-300",
                      )}
                    >
                      <Check className="w-4 h-4" />
                    </div>
                  </div>
                );
              })}
              {filteredKnowledge.length === 0 && (
                <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">
                  Ничего не найдено
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export function AgentsView() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [activeAgentId, setActiveAgentId] = useState("");
  const [, setForceUpdate] = useState(0);

  useEffect(() => {
    fetchAgents()
      .then((loaded) => {
        setAgents(loaded);
        if (loaded.length > 0) setActiveAgentId(loaded[0].id);
      })
      .catch(() => {});
  }, []);

  const activeAgent = agents.find((a) => a.id === activeAgentId) || agents[0];

  return (
    <div className="flex flex-col lg:flex-row items-start gap-6 h-[calc(100vh-8rem)] min-h-[700px]">
      {/* Left Sidebar - Agents List */}
      <div className="w-full lg:w-[320px] xl:w-[340px] shrink-0 flex flex-col h-full bg-white rounded-3xl border border-gray-200/60 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-100 bg-slate-50/50">
          <button className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all shadow-sm font-bold text-[14px]">
            <Plus className="w-5 h-5" />
            Создать агента
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          {agents.map((agent) => {
            const isActive = agent.id === activeAgentId;
            const Icon =
              agent.iconType === "zap"
                ? Zap
                : agent.iconType === "book"
                  ? BookOpen
                  : agent.iconType === "crown"
                    ? Crown
                    : Package;

            return (
              <div
                key={agent.id}
                onClick={() => setActiveAgentId(agent.id)}
                className={cn(
                  "p-4 rounded-2xl cursor-pointer transition-all group relative",
                  isActive
                    ? "bg-gray-50"
                    : "bg-transparent hover:bg-gray-50/50",
                )}
              >
                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm",
                      agent.iconBg,
                    )}
                  >
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-0.5">
                      <h3 className="font-bold text-gray-900 text-[15px] truncate pr-2">
                        {agent.name}
                      </h3>
                      <div
                        onClick={(e) => {
                          e.stopPropagation();
                          agent.isActive = !agent.isActive;
                          setForceUpdate((prev) => prev + 1);
                        }}
                        className={cn(
                          "w-8 h-4.5 rounded-full flex items-center px-0.5 transition-colors shrink-0",
                          agent.isActive ? "bg-indigo-500" : "bg-gray-200",
                        )}
                      >
                        <div
                          className={cn(
                            "w-3.5 h-3.5 rounded-full bg-white transition-transform shadow-sm",
                            agent.isActive && "translate-x-3.5",
                          )}
                        />
                      </div>
                    </div>
                    <p className="text-[12.5px] text-gray-500 font-medium truncate mb-2">
                      {agent.type}
                    </p>
                    <div className="flex gap-1.5 flex-wrap">
                      {agent.channels.map((c) => (
                        <span
                          key={c}
                          className="text-gray-600 bg-white border border-gray-200/60 px-2 py-0.5 rounded-md text-[10px] font-bold shadow-sm"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right Content - Editor */}
      <div className="flex-1 min-w-0 h-full overflow-y-auto">
        {activeAgent ? (
          <AgentEditor
            key={activeAgent.id}
            agent={activeAgent}
            onToggleActive={() => setForceUpdate((prev) => prev + 1)}
          />
        ) : (
          <div className="bg-white rounded-3xl shadow-sm border border-gray-200/60 flex items-center justify-center min-h-[400px] text-gray-400 text-[14px] font-medium">
            Загрузка агента…
          </div>
        )}
      </div>
    </div>
  );
}

// A simple Zap icon wrapper for use in map
function Zap(props: any) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14H4z" />
    </svg>
  );
}
