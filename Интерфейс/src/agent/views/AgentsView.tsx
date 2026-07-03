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
import {
  AccessMember,
  AccessTier,
  AgentDetail,
  BitrixUser,
  McpTool,
  TIER_LABELS,
  addAgentInstruction,
  createAgent,
  deleteAgent,
  deleteAgentInstruction,
  fetchAccessMembers,
  fetchAgentDetail,
  fetchAgents,
  fetchBitrixUsers,
  fetchKnowledge,
  fetchMcpTools,
  updateAgent,
  upsertAccess,
} from "../api";
import { AgentConfig, KnowledgeItem } from "../types";
import { cn } from "../../lib/utils";

const toolIcon = (name: string): string => {
  if (/bitrix|task/.test(name)) return "📝";
  if (/zoom|call/.test(name)) return "🎥";
  if (/google|sheet|drive|doc|file/.test(name)) return "📊";
  if (/knowledge|search|context/.test(name)) return "📚";
  if (/chat|message|dialog/.test(name)) return "💬";
  if (/report|analytic|index|period/.test(name)) return "📈";
  if (/employee|org|team|user|absence|access/.test(name)) return "👥";
  if (/instruction|capabilit|prompt/.test(name)) return "🧠";
  return "🔧";
};

const TOOL_TIER_CHIP: Record<string, string> = {
  ops: "bg-emerald-50 border-emerald-100 text-emerald-600",
  faq: "bg-indigo-50 border-indigo-100 text-indigo-600",
  admin: "bg-amber-50 border-amber-100 text-amber-600",
};

const AgentEditor: React.FC<{ agent: any; onToggleActive: () => void }> = ({
  agent,
  onToggleActive,
}) => {
  const [activeChannels, setActiveChannels] = useState<string[]>(
    agent.channels,
  );
  const [tools, setTools] = useState<McpTool[]>([]);
  const [toolsLoading, setToolsLoading] = useState(true);

  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItem[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(true);

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
    fetchMcpTools()
      .then(setTools)
      .catch(() => {})
      .finally(() => setToolsLoading(false));
    fetchKnowledge()
      .then(setKnowledgeItems)
      .catch(() => {})
      .finally(() => setKnowledgeLoading(false));
    // Live refresh: access grants can change from the Настройки tab or another
    // browser window; stats and the knowledge library drift over time too.
    const timer = window.setInterval(() => {
      fetchAccessMembers().then(setMembers).catch(() => {});
      fetchMcpTools().then(setTools).catch(() => {});
      fetchKnowledge().then(setKnowledgeItems).catch(() => {});
    }, 60000);
    return () => window.clearInterval(timer);
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

  const changeTier = (m: AccessMember, tier: AccessTier | "none") => {
    setActiveDropdown(null);
    void runAccessOp(() => upsertAccess(m.bitrix_user_id, tier, memberName(m)));
  };

  // Explicit "none" row = the bot stops responding (deleting the row would
  // silently fall back to the default faq level instead).
  const removeMember = (m: AccessMember) => {
    void runAccessOp(() => upsertAccess(m.bitrix_user_id, "none", memberName(m)));
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

  const filteredTools = tools.filter(
    (t) =>
      t.name.toLowerCase().includes(searchMcp.toLowerCase()) ||
      t.description.toLowerCase().includes(searchMcp.toLowerCase()),
  );

  const filteredKnowledge = knowledgeItems.filter(
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
                          <div className="absolute top-full left-0 mt-1 w-40 bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-20">
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
                            <button
                              onClick={() => changeTier(m, "none")}
                              className="w-full text-left px-3 py-1.5 text-[11px] font-bold text-rose-500 hover:bg-rose-50 transition-colors border-t border-gray-50"
                            >
                              Нет доступа (бот молчит)
                            </button>
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
                {tools.length} активно
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
              {toolsLoading && (
                <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">
                  Загрузка инструментов…
                </div>
              )}
              {filteredTools.map((tool) => (
                <div
                  key={tool.name}
                  title={tool.description}
                  className="flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl bg-white border border-gray-100 shadow-sm group"
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    {toolIcon(tool.name)}
                  </div>
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="flex items-center gap-2.5 mb-0.5">
                      <span className="font-bold text-gray-900 text-[14px] truncate">
                        {tool.name}
                      </span>
                      <div className="gap-1.5 hidden sm:flex shrink-0">
                        {tool.tiers.length === 1 && tool.tiers[0] === "admin" ? (
                          <span className={cn("text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm", TOOL_TIER_CHIP.admin)}>
                            только админ
                          </span>
                        ) : (
                          tool.tiers
                            .filter((t) => t !== "admin")
                            .map((t) => (
                              <span
                                key={t}
                                className={cn(
                                  "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm",
                                  TOOL_TIER_CHIP[t] || TOOL_TIER_CHIP.ops,
                                )}
                              >
                                {t}
                              </span>
                            ))
                        )}
                        {tool.core && (
                          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md bg-slate-100 border border-slate-200 text-slate-600 shadow-sm">
                            ядро
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">
                      {tool.description}
                    </p>
                  </div>
                  <div
                    title="Инструмент подключён. Выборочное отключение появится вместе с субагентами."
                    className="w-10 h-6 rounded-full flex items-center px-0.5 bg-indigo-500 shrink-0 cursor-default opacity-90"
                  >
                    <div className="w-5 h-5 rounded-full bg-white shadow-sm translate-x-4" />
                  </div>
                </div>
              ))}
              {!toolsLoading && filteredTools.length === 0 && (
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
                {knowledgeItems.length} подключено
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
              {knowledgeLoading && (
                <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">
                  Загрузка базы знаний…
                </div>
              )}
              {filteredKnowledge.map((k) => (
                <div
                  key={k.id}
                  title={k.description}
                  className="flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl bg-white border border-gray-100 shadow-sm group"
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    {k.type === "Скилл" ? "🔧" : "💬"}
                  </div>
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-bold text-gray-900 text-[14px] truncate">
                        {k.title}
                      </span>
                      <div className="gap-1.5 hidden sm:flex shrink-0">
                        <span
                          className={cn(
                            "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm",
                            k.type === "Скилл"
                              ? "bg-slate-100 border-slate-200 text-slate-600"
                              : "bg-indigo-50 border-indigo-100 text-indigo-600",
                          )}
                        >
                          {k.type === "Скилл" ? "скилл" : "инструкция"}
                        </span>
                        {k.type === "Скилл" && k.custom && (
                          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md bg-emerald-50 border border-emerald-100 text-emerald-600 shadow-sm">
                            свой · в github
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">
                      {k.description}
                    </p>
                  </div>
                  <div
                    title="Подключено у агента. Выборочный набор появится вместе с субагентами."
                    className="w-6 h-6 rounded-md border flex items-center justify-center shrink-0 bg-indigo-500 border-indigo-500 text-white shadow-sm cursor-default"
                  >
                    <Check className="w-4 h-4" />
                  </div>
                </div>
              ))}
              {!knowledgeLoading && filteredKnowledge.length === 0 && (
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

const initials2 = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((n) => n[0])
    .join("");

const CreateAgentModal: React.FC<{
  onClose: () => void;
  onCreated: (slug: string, warnings: string[]) => void;
}> = ({ onClose, onCreated }) => {
  const [name, setName] = useState("");
  const [tier, setTier] = useState<"faq" | "ops">("faq");
  const [rolePrompt, setRolePrompt] = useState("");
  const [members, setMembers] = useState<BitrixUser[]>([]);
  const [users, setUsers] = useState<BitrixUser[]>([]);
  const [userQuery, setUserQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchBitrixUsers().then(setUsers).catch(() => {});
  }, []);

  const memberIds = new Set(members.map((m) => m.id));
  const candidates = users
    .filter((u) => !memberIds.has(u.id) && u.name.toLowerCase().includes(userQuery.toLowerCase()))
    .slice(0, 8);

  const submit = () => {
    if (!name.trim()) {
      setError("Укажите имя агента.");
      return;
    }
    setBusy(true);
    setError("");
    createAgent({ name: name.trim(), tier, role_prompt: rolePrompt.trim(), members: members.map((m) => m.id) })
      .then((res) => onCreated(res.slug, res.warnings || []))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-white rounded-3xl shadow-2xl border border-gray-100 w-full max-w-xl p-8 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-bold text-gray-900">Новый агент</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-5">
          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Имя агента</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например: Агент склада"
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
            />
            <p className="text-[11.5px] font-medium text-gray-400 mt-1.5">
              Bitrix-бот с этим именем зарегистрируется автоматически и появится в мессенджере портала.
            </p>
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Уровень инструментов</label>
            <div className="flex p-1 bg-gray-50/80 border border-gray-200/60 rounded-2xl shadow-sm">
              {([
                { v: "faq", label: "База знаний", hint: "только чтение — безопасно" },
                { v: "ops", label: "Все функции", hint: "задачи, документы, действия" },
              ] as const).map((t) => (
                <button
                  key={t.v}
                  onClick={() => setTier(t.v)}
                  className={cn(
                    "flex flex-col items-center justify-center py-2.5 rounded-xl transition-all flex-1",
                    tier === t.v ? "bg-white text-gray-900 shadow-sm border border-gray-100" : "text-gray-500 hover:text-gray-700",
                  )}
                >
                  <span className="font-bold text-[13.5px]">{t.label}</span>
                  <span className="text-[10.5px] font-medium text-gray-400">{t.hint}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Роль и системный промпт</label>
            <textarea
              rows={4}
              value={rolePrompt}
              onChange={(e) => setRolePrompt(e.target.value)}
              placeholder="Ты — помощник склада. Отвечаешь только по остаткам, поставкам и задачам склада…"
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-[13.5px] focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all resize-none font-medium leading-relaxed shadow-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">
              Кому доступен <span className="text-gray-400 font-medium">(пусто = всем, у кого есть доступ к агентам)</span>
            </label>
            <div className="p-2.5 bg-gray-50/80 border border-gray-200/80 rounded-2xl shadow-sm">
              <div className="flex flex-wrap gap-2 mb-2">
                {members.map((m) => (
                  <span key={m.id} className="pl-2 pr-1.5 py-1 bg-white border border-gray-200/80 rounded-lg flex items-center gap-1.5 text-[12.5px] font-bold text-gray-700 shadow-sm">
                    {m.name}
                    <button onClick={() => setMembers((prev) => prev.filter((x) => x.id !== m.id))} className="text-gray-300 hover:text-rose-500">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </span>
                ))}
              </div>
              <input
                type="text"
                value={userQuery}
                onChange={(e) => setUserQuery(e.target.value)}
                placeholder="Поиск сотрудника…"
                className="w-full bg-transparent border-none outline-none text-[13px] font-medium text-gray-700 placeholder:text-gray-400 py-1 px-1"
              />
              {userQuery && (
                <div className="mt-1 space-y-0.5">
                  {candidates.map((u) => (
                    <button
                      key={u.id}
                      onClick={() => {
                        setMembers((prev) => [...prev, u]);
                        setUserQuery("");
                      }}
                      className="w-full text-left px-2 py-1.5 hover:bg-white rounded-lg text-[13px] font-bold text-gray-700 transition-colors"
                    >
                      {u.name}
                      {u.position && <span className="text-gray-400 font-medium"> · {u.position}</span>}
                    </button>
                  ))}
                  {candidates.length === 0 && (
                    <div className="px-2 py-1.5 text-[12px] font-medium text-gray-400">Не найдено</div>
                  )}
                </div>
              )}
            </div>
          </div>

          {error && <div className="text-[13px] font-bold text-rose-500">{error}</div>}

          <div className="flex gap-3 pt-2">
            <button
              onClick={submit}
              disabled={busy}
              className="flex-1 py-3 bg-indigo-600 text-white rounded-xl text-sm font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all disabled:opacity-60"
            >
              {busy ? "Создаю… (регистрирую бота в Bitrix)" : "Создать агента"}
            </button>
            <button onClick={onClose} className="px-6 py-3 bg-white text-gray-700 border border-gray-200 rounded-xl text-sm font-bold hover:bg-gray-50 transition-all shadow-sm">
              Отмена
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

const SubagentEditor: React.FC<{
  slug: string;
  onChanged: () => void;
  onDeleted: () => void;
}> = ({ slug, onChanged, onDeleted }) => {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [name, setName] = useState("");
  const [rolePrompt, setRolePrompt] = useState("");
  const [users, setUsers] = useState<BitrixUser[]>([]);
  const [userQuery, setUserQuery] = useState("");
  const [showUserSearch, setShowUserSearch] = useState(false);
  const [instName, setInstName] = useState("");
  const [instContent, setInstContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const load = () =>
    fetchAgentDetail(slug)
      .then((d) => {
        setDetail(d);
        setName(d.name);
        setRolePrompt(d.role_prompt);
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    void load();
    fetchBitrixUsers().then(setUsers).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const run = async (op: () => Promise<void>, refreshList = false) => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await op();
      await load();
      if (refreshList) onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) {
    return (
      <div className="bg-white rounded-3xl shadow-sm border border-gray-200/60 flex items-center justify-center min-h-[400px] text-gray-400 text-[14px] font-medium">
        {error || "Загрузка агента…"}
      </div>
    );
  }

  const memberIds = new Set(detail.members.map((m) => m.id));
  const candidates = users.filter(
    (u) => !memberIds.has(u.id) && u.name.toLowerCase().includes(userQuery.toLowerCase()),
  );
  const selfCount = detail.instructions.filter((i) => i.source === "self").length;

  return (
    <div className="bg-white rounded-3xl shadow-sm border border-gray-200/60 flex flex-col">
      <div className="bg-slate-50/50 p-6 md:px-8 border-b border-gray-100 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center shadow-sm bg-blue-100 text-blue-500">
            <Package className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-3 mb-0.5">
              <h2 className="text-xl font-bold text-gray-900 leading-none">{detail.name}</h2>
              <button
                onClick={() => run(() => updateAgent(slug, { is_active: !detail.is_active }), true)}
                className={cn(
                  "px-2 py-0.5 border text-[10px] font-bold rounded-md uppercase tracking-wider transition-colors",
                  detail.is_active
                    ? "bg-emerald-50 border-emerald-100 text-emerald-600 hover:bg-emerald-100"
                    : "bg-gray-100 border-gray-200 text-gray-500 hover:bg-gray-200",
                )}
              >
                {detail.is_active ? "включён" : "выключен"}
              </button>
              <span
                className={cn(
                  "px-2 py-0.5 border text-[10px] font-bold rounded-md uppercase tracking-wider",
                  detail.bitrix_bot_id
                    ? "bg-indigo-50 border-indigo-100 text-indigo-600"
                    : "bg-rose-50 border-rose-100 text-rose-600",
                )}
              >
                {detail.bitrix_bot_id ? `bitrix-бот #${detail.bitrix_bot_id}` : "бот не зарегистрирован"}
              </span>
            </div>
            <p className="text-sm text-gray-500 font-medium mt-1">
              субагент · {detail.tier === "ops" ? "все функции" : "база знаний"} · самообучение включено
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (window.confirm(`Удалить агента «${detail.name}»? Бот в Bitrix будет разрегистрирован, его инструкции удалятся.`)) {
                void run(() => deleteAgent(slug)).then(onDeleted);
              }
            }}
            className="px-5 py-2.5 bg-white text-rose-600 border border-rose-200 rounded-xl text-sm font-bold hover:bg-rose-50 transition-all shadow-sm"
          >
            Удалить
          </button>
          <button
            onClick={() =>
              run(async () => {
                await updateAgent(slug, { name: name.trim(), role_prompt: rolePrompt.trim() });
                setSaved(true);
              }, true)
            }
            disabled={busy}
            className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all disabled:opacity-60"
          >
            {saved ? "Сохранено ✓" : "Сохранить"}
          </button>
        </div>
      </div>

      <div className="p-6 md:p-8 flex flex-col xl:flex-row gap-8">
        <div className="flex-1 space-y-8 min-w-0">
          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Имя агента</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Роль и системный промпт</label>
            <textarea
              rows={6}
              value={rolePrompt}
              onChange={(e) => setRolePrompt(e.target.value)}
              className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-[13.5px] focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all resize-y font-medium leading-relaxed shadow-sm"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-bold text-gray-900">Команда и доступы</label>
              <span className="text-xs font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-md">
                {detail.members.length === 0 ? "открыт всем с доступом" : `${detail.members.length} сотрудников`}
              </span>
            </div>
            <div className={cn("p-2.5 bg-gray-50/80 border border-gray-200/80 rounded-2xl flex flex-wrap gap-2 items-center shadow-sm", busy && "opacity-60 pointer-events-none")}>
              {detail.members.map((m) => (
                <div key={m.id} className="pl-1.5 pr-2 py-1.5 bg-white border border-gray-200/80 rounded-xl flex items-center gap-2 shadow-sm">
                  <div className="w-7 h-7 rounded-lg bg-blue-50 border border-blue-100 text-blue-700 flex items-center justify-center text-[10px] font-bold shrink-0">
                    {initials2(m.name)}
                  </div>
                  <span className="text-[13px] font-bold text-gray-800">{m.name}</span>
                  <button
                    onClick={() => run(() => updateAgent(slug, { members: detail.members.filter((x) => x.id !== m.id).map((x) => x.id) }), true)}
                    className="text-gray-400 hover:text-rose-500 transition-colors bg-gray-50 hover:bg-rose-50 rounded-md p-1 shrink-0"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <div className="flex-1 min-w-[140px] flex items-center gap-2 px-2 relative">
                <User className="w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Добавить…"
                  value={userQuery}
                  onChange={(e) => {
                    setUserQuery(e.target.value);
                    setShowUserSearch(true);
                  }}
                  onFocus={() => setShowUserSearch(true)}
                  className="w-full bg-transparent border-none outline-none text-[13.5px] font-bold text-gray-700 placeholder:text-gray-400 placeholder:font-medium py-1.5"
                />
                {showUserSearch && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setShowUserSearch(false)} />
                    <div className="absolute top-full left-0 mt-2 w-full min-w-[200px] bg-white rounded-2xl shadow-xl border border-gray-100 py-1.5 z-20 max-h-48 overflow-y-auto">
                      {candidates.length > 0 ? (
                        candidates.map((u) => (
                          <button
                            key={u.id}
                            onClick={() => {
                              setShowUserSearch(false);
                              setUserQuery("");
                              void run(() => updateAgent(slug, { members: [...detail.members.map((x) => x.id), u.id] }), true);
                            }}
                            className="w-full text-left px-3 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
                          >
                            <div className="w-6 h-6 rounded-md bg-gray-100 text-gray-600 flex items-center justify-center text-[9px] font-bold">
                              {initials2(u.name)}
                            </div>
                            <span className="text-[13px] font-bold text-gray-700 truncate">{u.name}</span>
                          </button>
                        ))
                      ) : (
                        <div className="px-3 py-4 text-center text-[12px] font-medium text-gray-400">
                          {users.length === 0 ? "Загрузка…" : "Не найдено"}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {error && <div className="text-[13px] font-bold text-rose-500">{error}</div>}
        </div>

        {/* Right: personal instructions (owner + self-learned) */}
        <div className="flex-1 min-w-0">
          <div className="bg-slate-50/50 p-6 rounded-3xl border border-gray-100 flex flex-col min-h-[380px]">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2 text-gray-900 font-bold text-[15px]">
                <BookOpen className="w-5 h-5 text-indigo-500" />
                Личные инструкции и навыки
              </div>
              <span className="text-[11px] font-bold text-indigo-600 bg-indigo-50 px-2.5 py-0.5 rounded-md border border-indigo-100">
                {detail.instructions.length} · сам выучил {selfCount}
              </span>
            </div>

            <div className="space-y-2 mb-4 max-h-[320px] overflow-y-auto pr-1 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
              {detail.instructions.map((i) => (
                <div key={i.id} title={i.content} className="flex items-center p-3 rounded-2xl bg-white border border-gray-100 shadow-sm">
                  <div className="w-9 h-9 rounded-xl bg-white flex items-center justify-center text-lg mr-3 shrink-0 border border-gray-100 shadow-sm">
                    {i.source === "self" ? "🤖" : "💬"}
                  </div>
                  <div className="flex-1 min-w-0 pr-3">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-gray-900 text-[13.5px] truncate">{i.name}</span>
                      <span
                        className={cn(
                          "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shrink-0",
                          i.source === "self"
                            ? "bg-emerald-50 border-emerald-100 text-emerald-600"
                            : "bg-indigo-50 border-indigo-100 text-indigo-600",
                        )}
                      >
                        {i.source === "self" ? "сам выучил" : "владелец"}
                      </span>
                    </div>
                    <p className="text-[12px] font-medium text-gray-500 truncate">{i.content}</p>
                  </div>
                  <button
                    onClick={() => run(() => deleteAgentInstruction(slug, i.id))}
                    title="Удалить инструкцию"
                    className="text-gray-300 hover:text-rose-500 transition-colors p-1 shrink-0"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
              {detail.instructions.length === 0 && (
                <div className="text-center text-gray-400 text-[13px] font-medium py-6">
                  Пока пусто — агент будет накапливать навыки сам, или добавьте вручную ниже
                </div>
              )}
            </div>

            <div className="mt-auto space-y-2 border-t border-gray-100 pt-4">
              <input
                type="text"
                value={instName}
                onChange={(e) => setInstName(e.target.value)}
                placeholder="Название инструкции"
                className="w-full px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-bold focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
              />
              <textarea
                rows={2}
                value={instContent}
                onChange={(e) => setInstContent(e.target.value)}
                placeholder="Текст инструкции для агента…"
                className="w-full px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all resize-none shadow-sm"
              />
              <button
                onClick={() => {
                  if (!instName.trim() || !instContent.trim()) return;
                  void run(async () => {
                    await addAgentInstruction(slug, instName.trim(), instContent.trim());
                    setInstName("");
                    setInstContent("");
                  });
                }}
                disabled={busy || !instName.trim() || !instContent.trim()}
                className="w-full py-2.5 bg-white text-indigo-600 border border-indigo-200 rounded-xl text-[13px] font-bold hover:bg-indigo-50 transition-all shadow-sm disabled:opacity-50"
              >
                <Plus className="w-4 h-4 inline mr-1 -mt-0.5" />
                Добавить инструкцию
              </button>
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
  const [showCreate, setShowCreate] = useState(false);
  const [createNote, setCreateNote] = useState("");
  const [, setForceUpdate] = useState(0);

  const reloadAgents = (selectFirst = false) =>
    fetchAgents()
      .then((loaded) => {
        setAgents(loaded);
        if ((selectFirst || !loaded.some((a) => a.id === activeAgentId)) && loaded.length > 0) {
          setActiveAgentId(loaded[0].id);
        }
      })
      .catch(() => {});

  useEffect(() => {
    void reloadAgents(true);
    const timer = window.setInterval(() => void reloadAgents(false), 60000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeAgent = agents.find((a) => a.id === activeAgentId) || agents[0];

  return (
    <div className="flex flex-col lg:flex-row items-start gap-6 h-[calc(100vh-14rem)] min-h-[560px]">
      {/* Left Sidebar - Agents List */}
      <div className="w-full lg:w-[320px] xl:w-[340px] shrink-0 flex flex-col h-full bg-white rounded-3xl border border-gray-200/60 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-100 bg-slate-50/50">
          <button
            onClick={() => setShowCreate(true)}
            className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-all shadow-sm font-bold text-[14px]"
          >
            <Plus className="w-5 h-5" />
            Создать агента
          </button>
          {createNote && (
            <div className="mt-2 text-[11.5px] font-bold text-amber-600 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
              {createNote}
            </div>
          )}
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
                          if (agent.isSystem) {
                            agent.isActive = !agent.isActive;
                            setForceUpdate((prev) => prev + 1);
                          } else {
                            void updateAgent(agent.id, { is_active: !agent.isActive }).then(() => reloadAgents());
                          }
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
          activeAgent.isSystem ? (
            <AgentEditor
              key={activeAgent.id}
              agent={activeAgent}
              onToggleActive={() => setForceUpdate((prev) => prev + 1)}
            />
          ) : (
            <SubagentEditor
              key={activeAgent.id}
              slug={activeAgent.id}
              onChanged={() => void reloadAgents()}
              onDeleted={() => void reloadAgents(true)}
            />
          )
        ) : (
          <div className="bg-white rounded-3xl shadow-sm border border-gray-200/60 flex items-center justify-center min-h-[400px] text-gray-400 text-[14px] font-medium">
            Загрузка агента…
          </div>
        )}
      </div>

      {showCreate && (
        <CreateAgentModal
          onClose={() => setShowCreate(false)}
          onCreated={(slug, warnings) => {
            setShowCreate(false);
            setCreateNote(warnings.length > 0 ? warnings.join(" · ") : "");
            void reloadAgents().then(() => setActiveAgentId(slug));
          }}
        />
      )}
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
