// «Работа с воронками»: выбор воронки, её сценарий, правила агента и живые решения.
//
// Владелец 25.07.2026: «сделаем инструмент „Работа с воронками“, внутри можно выбрать воронку и
// сценарий настраивать в неё, чтобы этим можно было прям управлять».
//
// Что здесь можно и чего сознательно нельзя. МОЖНО править текст шага (чего агент ждёт от клиента
// и что делает) — он уходит агенту в промпт, и формулировки для своих клиентов владелец знает
// лучше; можно остановить агента на воронке. НЕЛЬЗЯ менять условия и приоритеты правил: они
// завязаны на факты и закрыты тестами, правка мышкой вернула бы расползание поведения, из-за
// которого мы трижды за сутки ломали работу. Правила показаны с причиной появления каждого.
import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  ArrowRight,
  ShieldCheck,
  Radio,
  ChevronDown,
  ChevronRight,
  Save,
  RotateCcw,
  Power,
  History,
  Pencil,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { fetchJsonSafe } from "../../api/client";

interface Funnel {
  id: number;
  name: string;
  agent: boolean;
  customized_stages: number;
  enabled: boolean;
}

interface ChainStage {
  stage_id: string;
  title: string;
  trigger: string;
  deals: number;
  step: string;
  need: string;
  action: string;
  code_need: string;
  code_action: string;
  customized: boolean;
  updated_at: string;
  updated_by: string;
}

interface Rule {
  slot: string;
  priority: number;
  name: string;
  action: string;
  origin: string;
}

interface Decision {
  at: string;
  dialog_id: string;
  deal_id: number | null;
  slot: string;
  rule: string;
  action: string;
  origin: string;
  facts: Record<string, unknown>;
  outcome: string;
}

interface HistoryRow {
  at: string;
  stage_id: string;
  field: string;
  old: string;
  new: string;
  author: string;
}

const FIELD_LABEL: Record<string, string> = {
  need: "чего ждём от клиента",
  action: "что делает агент",
  trigger: "когда наступает этап",
  enabled: "агент на воронке",
};

export function FunnelView() {
  const [funnels, setFunnels] = useState<Funnel[]>([]);
  const [funnelId, setFunnelId] = useState<number | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [isAgentFunnel, setIsAgentFunnel] = useState(false);
  const [chain, setChain] = useState<ChainStage[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [invariants, setInvariants] = useState<string[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [openStage, setOpenStage] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ need: string; action: string; trigger: string } | null>(null);
  const [openDecision, setOpenDecision] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchJsonSafe("/api/agent-center/funnels", undefined, 60000)
      .then((d) => {
        const list: Funnel[] = d.funnels || [];
        setFunnels(list);
        const agentFunnel = list.find((f) => f.agent) || list[0];
        if (agentFunnel) setFunnelId(agentFunnel.id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const loadFunnel = useCallback((id: number) => {
    setRefreshing(true);
    Promise.all([
      fetchJsonSafe(`/api/agent-center/funnel/${id}/map`, undefined, 90000),
      fetchJsonSafe("/api/agent-center/funnel/decisions?limit=60", undefined, 60000),
    ])
      .then(([map, trace]) => {
        setChain(map.chain || []);
        setRules(map.rules || []);
        setInvariants(map.invariants || []);
        setEnabled(!!map.enabled);
        setIsAgentFunnel(!!map.agent);
        setDecisions(trace.decisions || []);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, []);

  useEffect(() => {
    if (funnelId === null) return;
    setOpenStage(null);
    setDraft(null);
    loadFunnel(funnelId);
  }, [funnelId, loadFunnel]);

  const openEditor = (stage: ChainStage) => {
    const same = openStage === stage.stage_id;
    setOpenStage(same ? null : stage.stage_id);
    setDraft(
      same
        ? null
        : { need: stage.need || "", action: stage.action || "", trigger: stage.trigger || "" },
    );
  };

  const saveStage = async (stage: ChainStage) => {
    if (funnelId === null || !draft) return;
    setSaving(true);
    try {
      await fetchJsonSafe(
        `/api/agent-center/funnel/${funnelId}/stage`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage_id: stage.stage_id, ...draft }),
        },
        60000,
      );
      setNotice(`Сценарий этапа «${stage.title}» сохранён — агент уже работает по нему.`);
      loadFunnel(funnelId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const resetStage = async (stage: ChainStage) => {
    if (funnelId === null) return;
    setSaving(true);
    try {
      await fetchJsonSafe(
        `/api/agent-center/funnel/${funnelId}/stage`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage_id: stage.stage_id, need: "", action: "", trigger: "" }),
        },
        60000,
      );
      setNotice(`Этап «${stage.title}» вернулся к базовому сценарию.`);
      setDraft(null);
      setOpenStage(null);
      loadFunnel(funnelId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const toggleAgent = async () => {
    if (funnelId === null) return;
    const next = !enabled;
    if (!next && !window.confirm("Остановить агента на этой воронке? Клиентам будут отвечать только люди.")) return;
    try {
      await fetchJsonSafe(
        `/api/agent-center/funnel/${funnelId}/enabled`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: next }),
        },
        60000,
      );
      setEnabled(next);
      setNotice(next ? "Агент включён на воронке." : "Агент остановлен: отвечают только люди.");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const loadHistory = async () => {
    if (funnelId === null) return;
    setShowHistory(!showHistory);
    if (showHistory) return;
    try {
      const d = await fetchJsonSafe(`/api/agent-center/funnel/${funnelId}/history`, undefined, 60000);
      setHistory(d.history || []);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const bySlot = rules.reduce<Record<string, Rule[]>>((acc, rule) => {
    (acc[rule.slot] = acc[rule.slot] || []).push(rule);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Работа с воронками</h2>
          <p className="text-sm text-gray-500">
            Выберите воронку, посмотрите цепочку и правила агента и настройте сценарий этапов.
            Всё, что здесь показано, — сам источник поведения агента, а не описание.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadHistory}
            className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            <History className="h-4 w-4" />
            История правок
          </button>
          <button
            onClick={() => funnelId !== null && loadFunnel(funnelId)}
            className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
            Обновить
          </button>
        </div>
      </div>

      {/* Выбор воронки */}
      <div className="flex flex-wrap gap-2">
        {funnels.map((f) => (
          <button
            key={f.id}
            onClick={() => setFunnelId(f.id)}
            className={cn(
              "rounded-lg border px-3 py-2 text-left text-sm transition",
              funnelId === f.id
                ? "border-indigo-400 bg-indigo-50 text-indigo-900"
                : "border-gray-200 text-gray-700 hover:border-indigo-300",
            )}
          >
            <div className="flex items-center gap-2">
              {f.name}
              {f.agent && (
                <span className="rounded-full bg-emerald-100 px-2 text-xs font-semibold text-emerald-700">
                  агент
                </span>
              )}
            </div>
            {f.customized_stages > 0 && (
              <div className="text-xs text-gray-400">настроено этапов: {f.customized_stages}</div>
            )}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {notice}
        </div>
      )}

      {/* Управление агентом на воронке */}
      {isAgentFunnel && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <Power className={cn("h-5 w-5", enabled ? "text-emerald-500" : "text-rose-500")} />
            <div>
              <div className="text-sm font-medium text-gray-900">
                {enabled ? "Агент работает на этой воронке" : "Агент остановлен — отвечают люди"}
              </div>
              <div className="text-xs text-gray-500">
                Выключатель действует сразу: агент перестаёт отвечать клиентам этой воронки.
              </div>
            </div>
          </div>
          <button
            onClick={toggleAgent}
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium text-white",
              enabled ? "bg-rose-500 hover:bg-rose-600" : "bg-emerald-600 hover:bg-emerald-700",
            )}
          >
            {enabled ? "Остановить агента" : "Включить агента"}
          </button>
        </div>
      )}

      {showHistory && (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-3 text-sm font-medium text-gray-700">История правок сценария</div>
          {history.length === 0 ? (
            <div className="py-4 text-sm text-gray-400">Правок пока не было.</div>
          ) : (
            <div className="space-y-2">
              {history.map((h, i) => (
                <div key={i} className="rounded-lg bg-gray-50 p-3 text-xs text-gray-600">
                  <div className="text-gray-500">
                    {h.at} · {h.author} · {h.stage_id || "воронка"} ·{" "}
                    {FIELD_LABEL[h.field] || h.field}
                  </div>
                  {h.old && <div className="mt-1 text-rose-600">− {h.old}</div>}
                  {h.new && <div className="text-emerald-700">+ {h.new}</div>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Цепочка этапов с настройкой сценария */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="mb-4 text-sm font-medium text-gray-700">
          Логическая цепочка — нажмите этап, чтобы настроить сценарий
        </div>
        <div className="flex flex-wrap items-stretch gap-2">
          {chain.map((stage, index) => (
            <div key={stage.stage_id} className="flex items-stretch gap-2">
              <button
                onClick={() => openEditor(stage)}
                className={cn(
                  "min-w-[170px] rounded-lg border px-3 py-2 text-left transition",
                  openStage === stage.stage_id
                    ? "border-indigo-400 bg-indigo-50"
                    : "border-gray-200 hover:border-indigo-300",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-gray-900">{stage.title}</span>
                  <span className="flex items-center gap-1">
                    {stage.customized && <Pencil className="h-3 w-3 text-amber-500" />}
                    {stage.deals > 0 && (
                      <span className="rounded-full bg-indigo-100 px-2 text-xs font-semibold text-indigo-700">
                        {stage.deals}
                      </span>
                    )}
                  </span>
                </div>
                {stage.trigger && <div className="mt-1 text-xs text-gray-500">{stage.trigger}</div>}
              </button>
              {index < chain.length - 1 && (
                <ArrowRight className="mt-5 h-4 w-4 shrink-0 text-gray-300" />
              )}
            </div>
          ))}
        </div>

        {openStage && draft && (
          <div className="mt-4 space-y-3 rounded-lg bg-gray-50 p-4">
            {(() => {
              const stage = chain.find((s) => s.stage_id === openStage);
              if (!stage) return null;
              return (
                <>
                  <div className="text-sm font-medium text-gray-900">
                    {stage.title}
                    {stage.step && <span className="text-gray-500"> — шаг «{stage.step}»</span>}
                  </div>
                  {stage.customized && (
                    <div className="text-xs text-amber-600">
                      Настроено вручную {stage.updated_at} ({stage.updated_by})
                    </div>
                  )}
                  <label className="block text-xs text-gray-500">
                    Когда наступает этап
                    <input
                      value={draft.trigger}
                      onChange={(e) => setDraft({ ...draft, trigger: e.target.value })}
                      className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-800"
                      placeholder="например: клиент подтвердил анкету"
                    />
                  </label>
                  <label className="block text-xs text-gray-500">
                    Чего агент ждёт от клиента
                    <input
                      value={draft.need}
                      onChange={(e) => setDraft({ ...draft, need: e.target.value })}
                      className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-800"
                    />
                  </label>
                  <label className="block text-xs text-gray-500">
                    Что агент делает на этом этапе (этот текст уходит агенту в каждом сообщении)
                    <textarea
                      value={draft.action}
                      onChange={(e) => setDraft({ ...draft, action: e.target.value })}
                      rows={7}
                      className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 font-mono text-xs text-gray-800"
                    />
                  </label>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => saveStage(stage)}
                      disabled={saving}
                      className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                    >
                      <Save className="h-4 w-4" />
                      Сохранить
                    </button>
                    {stage.customized && (
                      <button
                        onClick={() => resetStage(stage)}
                        disabled={saving}
                        className="flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-white disabled:opacity-50"
                      >
                        <RotateCcw className="h-4 w-4" />
                        Вернуть базовый
                      </button>
                    )}
                    <span className="text-xs text-gray-400">
                      Правка применяется сразу и записывается в историю. Пустое поле = вернуть как в
                      коде.
                    </span>
                  </div>
                  {stage.code_action && stage.customized && (
                    <details className="text-xs text-gray-500">
                      <summary className="cursor-pointer">Базовый сценарий из кода</summary>
                      <div className="mt-1 whitespace-pre-wrap">{stage.code_action}</div>
                    </details>
                  )}
                </>
              );
            })()}
          </div>
        )}
      </section>

      {/* Инварианты */}
      {invariants.length > 0 && (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-700">
            <ShieldCheck className="h-4 w-4 text-emerald-500" />
            Что обязано быть верно всегда
          </div>
          <ul className="space-y-1.5">
            {invariants.map((line) => (
              <li key={line} className="flex gap-2 text-sm text-gray-600">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                {line}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-gray-400">
            Каждый пункт закреплён тестом: правка, которая его нарушит, не дойдёт до клиентов.
          </p>
        </section>
      )}

      {/* Правила — только чтение */}
      {rules.length > 0 && (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-1 text-sm font-medium text-gray-700">
            Правила, по которым агент принимает решения
          </div>
          <p className="mb-3 text-xs text-gray-400">
            Условия и приоритеты живут в коде и закрыты тестами — здесь только чтение. Менять
            формулировки можно выше, в сценарии этапов.
          </p>
          {Object.entries(bySlot).map(([slot, slotRules]) => (
            <div key={slot} className="mb-5 last:mb-0">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                {slot}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-400">
                      <th className="w-14 pb-2 font-normal">Приоритет</th>
                      <th className="pb-2 font-normal">Когда срабатывает</th>
                      <th className="pb-2 font-normal">Что делает агент</th>
                      <th className="pb-2 font-normal">Почему правило существует</th>
                    </tr>
                  </thead>
                  <tbody>
                    {slotRules.map((rule) => (
                      <tr key={rule.name} className="border-t border-gray-100 align-top">
                        <td className="py-2 text-gray-400">{rule.priority}</td>
                        <td className="py-2 pr-3 font-medium text-gray-900">{rule.name}</td>
                        <td className="py-2 pr-3 text-gray-600">{rule.action}</td>
                        <td className="py-2 text-xs text-gray-500">{rule.origin}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* Живые решения */}
      {isAgentFunnel && (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-700">
            <Radio className="h-4 w-4 text-rose-500" />
            Последние решения агента
          </div>
          {decisions.length === 0 ? (
            <div className="py-6 text-center text-sm text-gray-400">
              Решений пока нет — они появятся, как только агент ответит клиенту.
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {decisions.map((d, index) => (
                <div key={`${d.at}-${index}`} className="py-2">
                  <button
                    onClick={() => setOpenDecision(openDecision === index ? null : index)}
                    className="flex w-full items-start gap-3 text-left"
                  >
                    {openDecision === index ? (
                      <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-gray-400" />
                    ) : (
                      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-gray-400" />
                    )}
                    <span className="w-32 shrink-0 text-xs text-gray-400">{d.at}</span>
                    <span className="w-24 shrink-0 text-xs text-gray-500">{d.slot}</span>
                    <span className="flex-1 text-sm text-gray-900">{d.rule}</span>
                    <span className="hidden flex-1 text-sm text-gray-600 md:block">{d.action}</span>
                  </button>
                  {openDecision === index && (
                    <div className="ml-11 mt-2 space-y-1 rounded-lg bg-gray-50 p-3 text-xs text-gray-600">
                      <div>
                        <span className="text-gray-400">Клиент: </span>
                        {d.dialog_id}
                        {d.deal_id ? ` · сделка ${d.deal_id}` : " · сделки нет"}
                      </div>
                      <div>
                        <span className="text-gray-400">Что вышло: </span>
                        {d.outcome || "—"}
                      </div>
                      <div>
                        <span className="text-gray-400">Почему правило существует: </span>
                        {d.origin}
                      </div>
                      <div className="whitespace-pre-wrap">
                        <span className="text-gray-400">Факты на момент решения: </span>
                        {JSON.stringify(d.facts)}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
