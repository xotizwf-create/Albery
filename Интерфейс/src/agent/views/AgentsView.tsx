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
  AgentCapabilityConfig,
  AgentConfigKnowledge,
  AgentConfigTool,
  AgentDetail,
  AgentLevel,
  BitrixUser,
  LEVEL_LABELS,
  McpTool,
  TIER_LABELS,
  addAgentInstruction,
  createAgent,
  deleteAgent,
  deleteAgentInstruction,
  promoteAgentInstruction,
  fetchAccessMembers,
  fetchAgentConfig,
  fetchAgentDetail,
  fetchAgents,
  fetchBitrixUsers,
  fetchKnowledge,
  fetchMcpTools,
  registerAgentBot,
  saveAgentConfig,
  setInstructionScope,
  updateAgent,
  upsertAccess,
} from "../api";
import { AgentConfig, KnowledgeItem } from "../types";
import { agentSubSegments, setAgentPath } from "../route";
import { cn } from "../../lib/utils";

const AGENTS_BASE = "/agent";

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
  const [position, setPosition] = useState("");
  const [rolePrompt, setRolePrompt] = useState("");
  const [members, setMembers] = useState<BitrixUser[]>([]);
  const [users, setUsers] = useState<BitrixUser[]>([]);
  const [usersError, setUsersError] = useState("");
  const [userQuery, setUserQuery] = useState("");
  const [showUsers, setShowUsers] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchBitrixUsers()
      .then(setUsers)
      .catch((e: Error) => setUsersError(e.message));
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
    createAgent({ name: name.trim(), tier: "ops", position: position.trim(), role_prompt: rolePrompt.trim(), members: members.map((m) => m.id) })
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
          <div className="flex gap-4 flex-wrap">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-sm font-bold text-gray-900 mb-2">Имя агента</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Например: Агент склада"
                className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
              />
            </div>
            <div className="flex-1 min-w-[200px]">
              <label className="block text-sm font-bold text-gray-900 mb-2">Должность <span className="text-gray-400 font-medium">(в Bitrix)</span></label>
              <input
                type="text"
                value={position}
                onChange={(e) => setPosition(e.target.value)}
                placeholder="ИИ-агент Albery"
                className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
              />
            </div>
            <p className="w-full text-[11.5px] font-medium text-gray-400 -mt-1">
              Bitrix-бот с этим именем и должностью зарегистрируется автоматически и появится в мессенджере портала.
            </p>
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
                onFocus={() => setShowUsers(true)}
                placeholder="Нажмите, чтобы выбрать сотрудника…"
                className="w-full bg-transparent border-none outline-none text-[13px] font-medium text-gray-700 placeholder:text-gray-400 py-1 px-1"
              />
              {usersError && (
                <div className="px-2 py-1.5 text-[12px] font-bold text-rose-500">
                  Список сотрудников недоступен: {usersError}
                </div>
              )}
              {(showUsers || userQuery) && !usersError && (
                <div className="mt-1 space-y-0.5 max-h-44 overflow-y-auto">
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
                    <div className="px-2 py-1.5 text-[12px] font-medium text-gray-400">
                      {users.length === 0 ? "Загружаю сотрудников…" : "Не найдено"}
                    </div>
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

// The capability constructor for a subagent: MCP tool toggles + instruction/skill
// checklists, all backed by /api/agent-center/agents/<slug>/config. A disabled tool is
// removed from the agent's connector; an unselected instruction/skill is never injected
// into its turn. Mandatory (fixed) tools are shown locked-on.
const AgentCapabilityPanel: React.FC<{ slug: string; version: number }> = ({ slug, version }) => {
  const [config, setConfig] = useState<AgentCapabilityConfig | null>(null);
  const [tools, setTools] = useState<Set<string>>(new Set<string>());
  const [instr, setInstr] = useState<Set<string>>(new Set<string>());
  const [skills, setSkills] = useState<Set<string>>(new Set<string>());
  const [toolQuery, setToolQuery] = useState("");
  const [knowQuery, setKnowQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [scopeBusy, setScopeBusy] = useState("");

  useEffect(() => {
    fetchAgentConfig(slug)
      .then((c) => {
        setConfig(c);
        setTools(new Set(c.tools.filter((t) => t.enabled).map((t) => t.name)));
        setInstr(new Set(c.instructions.filter((i) => i.selected).map((i) => i.id)));
        setSkills(new Set(c.skills.filter((s) => s.selected).map((s) => s.id)));
      })
      .catch((e: Error) => setError(e.message));
  }, [slug, version]);

  if (!config) {
    return (
      <div className="border-t border-gray-100 p-6 md:p-8 text-gray-400 text-[13.5px] font-medium">
        {error || "Загрузка настроек…"}
      </div>
    );
  }

  const dirty = (() => {
    const enabledNow = new Set<string>(config.tools.filter((t) => t.enabled).map((t) => t.name));
    const selInstr = new Set<string>(config.instructions.filter((i) => i.selected).map((i) => i.id));
    const selSkills = new Set<string>(config.skills.filter((s) => s.selected).map((s) => s.id));
    const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((x) => b.has(x));
    return !(same(tools, enabledNow) && same(instr, selInstr) && same(skills, selSkills));
  })();

  const toggleTool = (t: AgentConfigTool) => {
    if (t.fixed) return;
    const turningOn = !tools.has(t.name);
    // Dangerous admin tools carry a confirm — the safety net that replaced the access level.
    if (turningOn && t.class === "admin" && !window.confirm(
      `«${t.name}» — опасный admin-инструмент (удаление данных / правка инструкций и настроек мозга всей компании). Включить его этому агенту?`,
    )) return;
    setTools((prev) => {
      const next = new Set(prev);
      next.has(t.name) ? next.delete(t.name) : next.add(t.name);
      return next;
    });
  };
  const toggleSet = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string) =>
    setter((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Flip an instruction universal<->optional. LIBRARY-level: affects every agent, so we
  // confirm and edit the registry immediately (separate from the per-agent Save button).
  const changeScope = async (i: AgentConfigKnowledge) => {
    const toUniversal = i.scope !== "universal";
    const next = toUniversal ? "universal" : "optional";
    if (!window.confirm(
      toUniversal
        ? `«${i.title}» станет УНИВЕРСАЛЬНОЙ — будет применяться у ВСЕХ агентов. Продолжить?`
        : `«${i.title}» станет ПО ВЫБОРУ — перестанет применяться автоматически и будет подключаться каждому агенту отдельно (у кого сейчас не подключена — потеряет её). Продолжить?`,
    )) return;
    setScopeBusy(i.id);
    setError("");
    try {
      await setInstructionScope(i.id, next);
      setConfig((prev) => prev && {
        ...prev,
        instructions: prev.instructions.map((x) =>
          x.id === i.id ? { ...x, scope: next, selected: toUniversal } : x),
      });
      setInstr((prev) => {
        const n = new Set(prev);
        toUniversal ? n.add(i.id) : n.delete(i.id);
        return n;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScopeBusy("");
    }
  };

  const save = async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await saveAgentConfig(slug, { tools: [...tools], instructions: [...instr], skills: [...skills] });
      const fresh = await fetchAgentConfig(slug);
      setConfig(fresh);
      setTools(new Set(fresh.tools.filter((t) => t.enabled).map((t) => t.name)));
      setInstr(new Set(fresh.instructions.filter((i) => i.selected).map((i) => i.id)));
      setSkills(new Set(fresh.skills.filter((s) => s.selected).map((s) => s.id)));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const filteredTools = config.tools.filter(
    (t) => t.name.toLowerCase().includes(toolQuery.toLowerCase()) || t.description.toLowerCase().includes(toolQuery.toLowerCase()),
  );
  const q = knowQuery.toLowerCase();
  const matchK = (k: AgentConfigKnowledge) =>
    k.title.toLowerCase().includes(q) || (k.parent || "").toLowerCase().includes(q) || k.description.toLowerCase().includes(q);
  const filteredInstr = config.instructions.filter(matchK);
  const filteredSkills = config.skills.filter(matchK);

  return (
    <div className="border-t border-gray-100 p-6 md:p-8">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <div>
          <h3 className="text-[15px] font-bold text-gray-900">Возможности агента</h3>
          <p className="text-[12.5px] font-medium text-gray-500 mt-0.5">
            Что агент видит и чем может пользоваться. Отключённое недоступно ему физически. Уровень:{" "}
            {LEVEL_LABELS[config.tier]}.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {saved && !dirty && <span className="text-[12.5px] font-bold text-emerald-600">Сохранено ✓</span>}
          <button
            onClick={save}
            disabled={busy || !dirty}
            className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all disabled:opacity-50"
          >
            {busy ? "Сохраняю…" : "Сохранить возможности"}
          </button>
        </div>
      </div>
      {error && <div className="text-[13px] font-bold text-rose-500 mb-4">{error}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* MCP tools */}
        <div className="bg-slate-50/50 p-6 rounded-3xl border border-gray-100 flex flex-col h-[420px]">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2 text-gray-900 font-bold text-[15px]">
              <Network className="w-5 h-5 text-emerald-500" />
              Инструменты (MCP)
            </div>
            <span className="text-[11px] font-bold text-emerald-600 bg-emerald-50 px-2.5 py-0.5 rounded-md border border-emerald-100">
              {tools.size} из {config.tools_total}
            </span>
          </div>
          <div className="relative mb-4 shrink-0">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Поиск инструментов…"
              value={toolQuery}
              onChange={(e) => setToolQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13.5px] font-medium focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none transition-all shadow-sm"
            />
          </div>
          <div className="overflow-y-auto pr-2 space-y-2 flex-1 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
            {filteredTools.map((t) => {
              const on = tools.has(t.name);
              const locked = t.fixed;
              return (
                <div
                  key={t.name}
                  title={t.description}
                  className="flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl bg-white border border-gray-100 shadow-sm"
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    {toolIcon(t.name)}
                  </div>
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-bold text-gray-900 text-[14px] truncate">{t.name}</span>
                      {t.class === "admin" && (
                        <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md bg-amber-50 border border-amber-100 text-amber-600 shadow-sm shrink-0">
                          admin
                        </span>
                      )}
                      {t.fixed && (
                        <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md bg-slate-100 border border-slate-200 text-slate-600 shadow-sm shrink-0">
                          базовый
                        </span>
                      )}
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">{t.description}</p>
                  </div>
                  <button
                    onClick={() => toggleTool(t)}
                    disabled={locked}
                    title={t.fixed ? "Базовый инструмент — всегда включён" : on ? "Отключить" : "Включить"}
                    className={cn(
                      "w-10 h-6 rounded-full flex items-center px-0.5 shrink-0 transition-colors",
                      on ? "bg-indigo-500" : "bg-gray-200",
                      locked ? "cursor-not-allowed opacity-70" : "cursor-pointer",
                    )}
                  >
                    <div className={cn("w-5 h-5 rounded-full bg-white shadow-sm transition-transform", on && "translate-x-4")} />
                  </button>
                </div>
              );
            })}
            {filteredTools.length === 0 && (
              <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">Ничего не найдено</div>
            )}
          </div>
        </div>

        {/* Instructions & skills from the library */}
        <div className="bg-slate-50/50 p-6 rounded-3xl border border-gray-100 flex flex-col h-[420px]">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2 text-gray-900 font-bold text-[15px]">
              <BookOpen className="w-5 h-5 text-indigo-500" />
              Инструкции и Скиллы
            </div>
            <span className="text-[11px] font-bold text-indigo-600 bg-indigo-50 px-2.5 py-0.5 rounded-md border border-indigo-100">
              {instr.size + skills.size} подключено
            </span>
          </div>
          <div className="relative mb-4 shrink-0">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Поиск по базе знаний…"
              value={knowQuery}
              onChange={(e) => setKnowQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13.5px] font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
            />
          </div>
          <div className="overflow-y-auto pr-2 space-y-2 flex-1 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
            {/* Instructions — with universal/optional scope. Universal ones apply to
                every agent (checkbox locked on); optional ones connect per-agent. */}
            {filteredInstr.map((i) => {
              const universal = i.scope === "universal";
              const on = universal || instr.has(i.id);
              return (
                <div
                  key={`instruction:${i.id}`}
                  title={i.description}
                  className="flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl bg-white border border-gray-100 shadow-sm"
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    💬
                  </div>
                  <div className="flex-1 min-w-0 pr-3">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      <span className="font-bold text-gray-900 text-[14px] truncate">
                        {i.parent ? `${i.parent} / ${i.title}` : i.title}
                      </span>
                      <span
                        className={cn(
                          "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm shrink-0",
                          universal
                            ? "bg-amber-50 border-amber-100 text-amber-600"
                            : "bg-indigo-50 border-indigo-100 text-indigo-600",
                        )}
                      >
                        {universal ? "для всех" : "по выбору"}
                      </span>
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">{i.description}</p>
                  </div>
                  <button
                    onClick={() => changeScope(i)}
                    disabled={scopeBusy === i.id}
                    title={universal ? "Сделать «по выбору» (подключать агентам вручную)" : "Сделать универсальной (у всех агентов)"}
                    className="text-[11px] font-bold text-gray-400 hover:text-indigo-600 mr-3 shrink-0 disabled:opacity-50 whitespace-nowrap"
                  >
                    {scopeBusy === i.id ? "…" : universal ? "→ по выбору" : "→ для всех"}
                  </button>
                  <button
                    onClick={() => !universal && toggleSet(setInstr, i.id)}
                    disabled={universal}
                    title={universal ? "Универсальная — применяется у всех агентов" : on ? "Отключить у этого агента" : "Подключить этому агенту"}
                    className={cn(
                      "w-6 h-6 rounded-md border flex items-center justify-center shrink-0 shadow-sm transition-colors",
                      on ? "bg-indigo-500 border-indigo-500 text-white" : "bg-white border-gray-300 text-transparent",
                      universal && "opacity-60 cursor-not-allowed",
                    )}
                  >
                    <Check className="w-4 h-4" />
                  </button>
                </div>
              );
            })}
            {/* Skills — connect per-agent (prompt-level; base skills carry a badge). */}
            {filteredSkills.map((s) => {
              const on = skills.has(s.id);
              return (
                <button
                  key={`skill:${s.id}`}
                  onClick={() => toggleSet(setSkills, s.id)}
                  title={s.description}
                  className="w-full text-left flex items-center p-3 sm:px-4 sm:py-3.5 rounded-2xl bg-white border border-gray-100 shadow-sm hover:border-indigo-200 transition-colors"
                >
                  <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center text-xl mr-3.5 shrink-0 border border-gray-100 shadow-sm">
                    🔧
                  </div>
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      <span className="font-bold text-gray-900 text-[14px] truncate">
                        {s.parent ? `${s.parent} / ${s.title}` : s.title}
                      </span>
                      <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm shrink-0 bg-slate-100 border-slate-200 text-slate-600">
                        скилл
                      </span>
                      {s.kind === "hermes_base" && (
                        <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-md border shadow-sm shrink-0 bg-slate-50 border-slate-200 text-slate-400">
                          базовый
                        </span>
                      )}
                    </div>
                    <p className="text-[12.5px] font-medium text-gray-500 truncate">{s.description}</p>
                  </div>
                  <div
                    className={cn(
                      "w-6 h-6 rounded-md border flex items-center justify-center shrink-0 shadow-sm transition-colors",
                      on ? "bg-indigo-500 border-indigo-500 text-white" : "bg-white border-gray-300 text-transparent",
                    )}
                  >
                    <Check className="w-4 h-4" />
                  </div>
                </button>
              );
            })}
            {filteredInstr.length + filteredSkills.length === 0 && (
              <div className="text-center text-gray-400 text-[13.5px] font-medium py-4">Ничего не найдено</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// The single unified editor for EVERY agent — the universal (main) one and subagents alike.
// Identical layout for all; only data differences (main can't be deleted, its team is the
// company-wide agent_access) are branched on `detail.is_main`.
const AgentEditor: React.FC<{
  slug: string;
  onChanged: () => void;
  onDeleted: () => void;
}> = ({ slug, onChanged, onDeleted }) => {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [name, setName] = useState("");
  const [position, setPosition] = useState("");
  const [rolePrompt, setRolePrompt] = useState("");
  const [users, setUsers] = useState<BitrixUser[]>([]);
  const [userQuery, setUserQuery] = useState("");
  const [showUserSearch, setShowUserSearch] = useState(false);
  const [instName, setInstName] = useState("");
  const [instContent, setInstContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [configVersion, setConfigVersion] = useState(0);

  const load = () =>
    fetchAgentDetail(slug)
      .then((d) => {
        setDetail(d);
        setName(d.name);
        setPosition(d.position || "");
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
              {detail.bitrix_bot_id ? (
                <span className="px-2 py-0.5 border text-[10px] font-bold rounded-md uppercase tracking-wider bg-indigo-50 border-indigo-100 text-indigo-600">
                  bitrix-бот #{detail.bitrix_bot_id}
                </span>
              ) : (
                <button
                  onClick={() =>
                    run(async () => {
                      const res = await registerAgentBot(slug);
                      if (res.warnings?.length) setError(res.warnings.join(" · "));
                    }, true)
                  }
                  disabled={busy}
                  className="px-2 py-0.5 border text-[10px] font-bold rounded-md uppercase tracking-wider bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100 transition-colors disabled:opacity-60"
                  title="Бот не зарегистрирован в Bitrix — нажмите, чтобы повторить регистрацию"
                >
                  ⚠ зарегистрировать бота
                </button>
              )}
            </div>
            <p className="text-sm text-gray-500 font-medium mt-1">
              {detail.is_main ? "универсальный агент · для всех сотрудников" : "субагент · самообучение включено"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {!detail.is_main && (
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
          )}
          <button
            onClick={() =>
              run(async () => {
                await updateAgent(slug, { name: name.trim(), position: position.trim(), role_prompt: rolePrompt.trim() });
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
          <div className="flex gap-4 flex-wrap">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-sm font-bold text-gray-900 mb-2">
                Имя агента <span className="text-gray-400 font-medium">(как в Bitrix)</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
              />
            </div>
            <div className="flex-1 min-w-[200px]">
              <label className="block text-sm font-bold text-gray-900 mb-2">
                Должность <span className="text-gray-400 font-medium">(синхр. с Bitrix)</span>
              </label>
              <input
                type="text"
                value={position}
                onChange={(e) => setPosition(e.target.value)}
                placeholder="ИИ-агент Albery"
                className="w-full px-4 py-3 bg-gray-50 border border-gray-200/80 rounded-xl text-sm focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all font-bold shadow-sm"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-bold text-gray-900 mb-2">Каналы связи</label>
            <div className="flex gap-3">
              <div className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-white border border-gray-200/80 text-gray-800 font-bold text-[13.5px] shadow-sm">
                <MessageSquare className="w-4 h-4" /> Bitrix24
              </div>
              <div className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-gray-50 border border-dashed border-gray-200 text-gray-400 font-bold text-[13.5px]">
                <Send className="w-4 h-4" /> Telegram
              </div>
            </div>
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
                    {(i.created_by || i.updated_by) && (
                      <p className="text-[10.5px] font-medium text-gray-400 truncate mt-0.5">
                        {i.created_by && <>создал: {i.created_by}{i.created ? ` · ${i.created}` : ""}</>}
                        {i.created_by && i.updated_by && i.updated_by !== i.created_by && " · "}
                        {i.updated_by && i.updated_by !== i.created_by && (
                          <>менял: {i.updated_by}{i.updated ? ` · ${i.updated}` : ""}</>
                        )}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => {
                      if (!window.confirm(`Повысить «${i.name}» до общей библиотеки? Она станет подключаемой (по выбору) к любому агенту. Личная копия останется у этого агента.`)) return;
                      void run(async () => { await promoteAgentInstruction(slug, i.id); });
                    }}
                    title="Повысить до общих (в библиотеку, подключать другим агентам)"
                    className="text-[10.5px] font-bold text-gray-400 hover:text-indigo-600 transition-colors px-1.5 py-1 shrink-0 whitespace-nowrap"
                  >
                    ↑ в общие
                  </button>
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

      <AgentCapabilityPanel slug={slug} version={configVersion} />
    </div>
  );
};

export function AgentsView() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  // Initial agent comes from the URL (/agent/<slug>) so a refresh keeps that agent open.
  const [activeAgentId, setActiveAgentId] = useState(() => agentSubSegments(AGENTS_BASE)[0] || "");
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
    // Don't force-select the first agent when the URL already names one to open.
    void reloadAgents(!agentSubSegments(AGENTS_BASE)[0]);
    const timer = window.setInterval(() => void reloadAgents(false), 60000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the URL in sync with the open agent (replaceState — survives refresh & shareable).
  useEffect(() => {
    setAgentPath(AGENTS_BASE, [activeAgentId || null]);
  }, [activeAgentId]);

  // Back/forward: re-read the open agent from the URL.
  useEffect(() => {
    const onPop = () => setActiveAgentId(agentSubSegments(AGENTS_BASE)[0] || "");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
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

      {/* Right Content - Editor: one unified editor for every agent (universal + subagents) */}
      <div className="flex-1 min-w-0 h-full overflow-y-auto">
        {activeAgent ? (
          <AgentEditor
            key={activeAgent.id}
            slug={activeAgent.id}
            onChanged={() => void reloadAgents()}
            onDeleted={() => void reloadAgents(true)}
          />
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
