// Воронка ИУ: логическая цепочка, правила агента и живые решения.
//
// Владелец 25.07.2026: «чтоб я увидел логическую цепочку и правила, по которым действует агент,
// чтоб визуально понимал что происходит». До этого поведение агента можно было понять только
// чтением кода: владелец видел сообщение клиенту и не видел причину.
//
// Страница показывает ровно то, что реально управляет агентом: этапы и шаги считаются тем же
// funnel_next_step, что уходит в промпт, правила — из реестра funnel_rules с причиной появления
// каждого, решения — из трассы agent_decisions. Никаких пересказов: если тут что-то написано,
// значит агент так и действует.
import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  ArrowRight,
  ShieldCheck,
  ListOrdered,
  Radio,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { fetchJsonSafe } from "../../api/client";

interface ChainStage {
  stage_id: string;
  title: string;
  trigger: string;
  deals: number;
  step?: string;
  need?: string;
  action?: string;
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

const REFRESH_MS = 30000;

export function FunnelView() {
  const [chain, setChain] = useState<ChainStage[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [invariants, setInvariants] = useState<string[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [openStage, setOpenStage] = useState<string | null>(null);
  const [openDecision, setOpenDecision] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setRefreshing(true);
    Promise.all([
      fetchJsonSafe("/api/agent-center/funnel/map", undefined, 60000),
      fetchJsonSafe("/api/agent-center/funnel/decisions?limit=60", undefined, 60000),
    ])
      .then(([map, trace]) => {
        setChain(map.chain || []);
        setRules(map.rules || []);
        setInvariants(map.invariants || []);
        setDecisions(trace.decisions || []);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const bySlot = rules.reduce<Record<string, Rule[]>>((acc, rule) => {
    (acc[rule.slot] = acc[rule.slot] || []).push(rule);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Воронка ИУ: что делает агент</h2>
          <p className="text-sm text-gray-500">
            Цепочка этапов, правила с причинами появления и живые решения агента. Всё считается
            тем же кодом, который управляет агентом, — это не описание, а сам источник.
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          Обновить
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* Цепочка этапов: клик по этапу раскрывает, чего агент ждёт и что делает. */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="mb-4 flex items-center gap-2 text-sm font-medium text-gray-700">
          <ListOrdered className="h-4 w-4 text-indigo-500" />
          Логическая цепочка
        </div>
        <div className="flex flex-wrap items-stretch gap-2">
          {chain.map((stage, index) => (
            <div key={stage.stage_id} className="flex items-stretch gap-2">
              <button
                onClick={() => setOpenStage(openStage === stage.stage_id ? null : stage.stage_id)}
                className={cn(
                  "min-w-[170px] rounded-lg border px-3 py-2 text-left transition",
                  openStage === stage.stage_id
                    ? "border-indigo-400 bg-indigo-50"
                    : "border-gray-200 hover:border-indigo-300",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-gray-900">{stage.title}</span>
                  {stage.deals > 0 && (
                    <span className="rounded-full bg-indigo-100 px-2 text-xs font-semibold text-indigo-700">
                      {stage.deals}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-gray-500">{stage.trigger}</div>
              </button>
              {index < chain.length - 1 && (
                <ArrowRight className="mt-5 h-4 w-4 shrink-0 text-gray-300" />
              )}
            </div>
          ))}
        </div>

        {openStage && (
          <div className="mt-4 rounded-lg bg-gray-50 p-4">
            {(() => {
              const stage = chain.find((s) => s.stage_id === openStage);
              if (!stage) return null;
              return (
                <div className="space-y-2 text-sm">
                  <div className="font-medium text-gray-900">
                    {stage.title} — шаг «{stage.step || "не описан"}»
                  </div>
                  <div className="text-gray-600">
                    <span className="text-gray-400">Ждёт от клиента: </span>
                    {stage.need || "—"}
                  </div>
                  <div className="whitespace-pre-wrap text-gray-600">
                    <span className="text-gray-400">Что делает: </span>
                    {stage.action || "—"}
                  </div>
                  <div className="text-xs text-gray-400">
                    Этот текст уходит агенту в каждом сообщении по такой сделке.
                  </div>
                </div>
              );
            })()}
          </div>
        )}
      </section>

      {/* Инварианты: то, что обязано быть верно всегда. */}
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

      {/* Правила: приоритет решает порядок, у каждого — причина появления. */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="mb-3 text-sm font-medium text-gray-700">
          Правила, по которым агент принимает решения
        </div>
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
        <p className="text-xs text-gray-400">
          Приоритет задан числом, а не порядком строк в коде: новое правило не сдвигает соседние.
        </p>
      </section>

      {/* Живые решения: цепочка в действии. */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-700">
          <Radio className="h-4 w-4 text-rose-500" />
          Последние решения агента
          <span className="text-xs font-normal text-gray-400">
            обновляется каждые 30 секунд
          </span>
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
    </div>
  );
}
