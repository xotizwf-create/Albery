import React, { useEffect, useState } from "react";
import { Clock, Play, Plus, Trash2 } from "lucide-react";
import {
  AgentAutomation,
  createAgentAutomation,
  deleteAgentAutomation,
  deleteRecurringTask,
  fetchAgentAutomations,
  runAgentAutomation,
  runRecurringTask,
  updateAgentAutomation,
  updateRecurringTask,
} from "../api";
import { cn } from "../../lib/utils";

// Sub-tab «Автоматизации» of the agent editor: the agent's own cron jobs. System rows
// mirror the legacy Hermes crons (read-only); agent rows are created by the owner here
// or by the agent itself from chat (schedule_my_automation) and run in-app.

const SCHEDULE_PRESETS: Array<{ label: string; cron: string }> = [
  { label: "каждый день в 9:00", cron: "0 9 * * *" },
  { label: "по будням в 9:00", cron: "0 9 * * 1-5" },
  { label: "по понедельникам в 10:00", cron: "0 10 * * 1" },
  { label: "по пятницам в 18:00", cron: "0 18 * * 5" },
  { label: "каждый час", cron: "0 * * * *" },
];

const statusChip = (a: AgentAutomation): { text: string; cls: string } => {
  if (!a.last_status) return { text: "ещё не запускалась", cls: "bg-gray-100 text-gray-500 border-gray-200" };
  if (a.last_status === "running") return { text: "выполняется…", cls: "bg-sky-50 text-sky-600 border-sky-100 animate-pulse" };
  if (a.last_status === "interrupted") return { text: `прервана · ${a.last_run}`, cls: "bg-amber-50 text-amber-600 border-amber-100" };
  if (a.last_status === "skipped") return { text: `пропущена · ${a.last_run}`, cls: "bg-amber-50 text-amber-600 border-amber-100" };
  if (a.last_status === "error") return { text: `ошибка · ${a.last_run}`, cls: "bg-rose-50 text-rose-600 border-rose-100" };
  if (a.last_status === "silent") return { text: `тихо · ${a.last_run}`, cls: "bg-gray-100 text-gray-500 border-gray-200" };
  return { text: `ок · ${a.last_run}`, cls: "bg-emerald-50 text-emerald-600 border-emerald-100" };
};

const sourceChip = (a: AgentAutomation): { text: string; cls: string } => {
  if (a.kind === "system") return { text: "системная · Hermes", cls: "bg-violet-50 text-violet-600 border-violet-100" };
  if (a.kind === "task") return { text: "регулярная задача 📋", cls: "bg-sky-50 text-sky-600 border-sky-100" };
  if (a.created_by === "self") return { text: "сам поставил 🤖", cls: "bg-emerald-50 text-emerald-600 border-emerald-100" };
  return { text: "владелец", cls: "bg-indigo-50 text-indigo-600 border-indigo-100" };
};

export const AutomationsPanel: React.FC<{ slug: string }> = ({ slug }) => {
  const [rows, setRows] = useState<AgentAutomation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | 0 | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [fName, setFName] = useState("");
  const [fSchedule, setFSchedule] = useState("0 9 * * 1-5");
  const [fPrompt, setFPrompt] = useState("");
  const [fDeliver, setFDeliver] = useState("");

  const load = () =>
    fetchAgentAutomations(slug)
      .then((r) => {
        setRows(r);
        setLoaded(true);
        setError("");
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    setLoaded(false);
    setOpenId(null);
    setShowForm(false);
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const act = async (id: number | 0, op: () => Promise<void>) => {
    setBusyId(id);
    setError("");
    try {
      await op();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 md:p-8 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <p className="text-[13px] font-medium text-gray-500 max-w-2xl">
          Всё регулярное у этого агента: автоматизации (агент выполняет по расписанию своими
          инструментами и присылает результат в Битрикс) и регулярные задачи 📋 (приложение само
          создаёт задачу в Bitrix точно в срок). Агент ставит и то и другое из чата — просто попросите.
        </p>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="px-4 py-2.5 bg-indigo-600 text-white rounded-xl text-[13px] font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all"
        >
          <Plus className="w-4 h-4 inline mr-1 -mt-0.5" />
          Добавить автоматизацию
        </button>
      </div>

      {showForm && (
        <div className="bg-slate-50/70 border border-gray-200/80 rounded-2xl p-5 space-y-3">
          <div className="flex gap-3 flex-wrap">
            <input
              type="text"
              value={fName}
              onChange={(e) => setFName(e.target.value)}
              placeholder="Название (например «Утренняя сводка задач»)"
              className="flex-1 min-w-[220px] px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-bold focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none shadow-sm"
            />
            <div className="flex items-center gap-2">
              <select
                value={SCHEDULE_PRESETS.some((p) => p.cron === fSchedule) ? fSchedule : "custom"}
                onChange={(e) => e.target.value !== "custom" && setFSchedule(e.target.value)}
                className="px-3 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-bold text-gray-700 outline-none shadow-sm"
              >
                {SCHEDULE_PRESETS.map((p) => (
                  <option key={p.cron} value={p.cron}>
                    {p.label}
                  </option>
                ))}
                <option value="custom">свой cron…</option>
              </select>
              <input
                type="text"
                value={fSchedule}
                onChange={(e) => setFSchedule(e.target.value)}
                title="5 полей cron (мин час день месяц день-недели), время МСК"
                className="w-32 px-3 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-mono font-bold focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none shadow-sm"
              />
            </div>
          </div>
          <textarea
            rows={3}
            value={fPrompt}
            onChange={(e) => setFPrompt(e.target.value)}
            placeholder="Задача: что агент должен делать при каждом запуске (данные берёт из своих инструментов)…"
            className="w-full px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none resize-y shadow-sm"
          />
          <div className="flex gap-3 flex-wrap items-center">
            <input
              type="text"
              value={fDeliver}
              onChange={(e) => setFDeliver(e.target.value)}
              placeholder="Куда слать: ID сотрудника или chatN (пусто — чат уведомлений)"
              className="flex-1 min-w-[260px] px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none shadow-sm"
            />
            <button
              onClick={() =>
                void act(0, async () => {
                  await createAgentAutomation(slug, {
                    name: fName.trim(),
                    schedule: fSchedule.trim(),
                    prompt: fPrompt.trim(),
                    deliver_to: fDeliver.trim(),
                  });
                  setFName("");
                  setFPrompt("");
                  setShowForm(false);
                })
              }
              disabled={busyId !== null || !fName.trim() || !fPrompt.trim()}
              className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-[13px] font-bold hover:bg-indigo-700 shadow-sm transition-all disabled:opacity-50"
            >
              Создать
            </button>
          </div>
        </div>
      )}

      {error && <div className="text-[13px] font-bold text-rose-500">{error}</div>}

      <div className="space-y-2.5">
        {rows.map((a) => {
          const st = statusChip(a);
          const src = sourceChip(a);
          const open = openId === a.id;
          const system = a.kind === "system";
          return (
            <div key={a.id} className={cn("bg-white border border-gray-200/70 rounded-2xl shadow-sm overflow-hidden", !a.is_active && "opacity-60")}>
              <div className="p-4 flex items-center gap-3 cursor-pointer" onClick={() => setOpenId(open ? null : a.id)}>
                <div className="w-9 h-9 rounded-xl bg-indigo-50 border border-indigo-100 text-indigo-500 flex items-center justify-center shrink-0">
                  <Clock className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-bold text-gray-900 text-[13.5px]">{a.name}</span>
                    <span className={cn("text-[9.5px] font-bold uppercase px-1.5 py-0.5 rounded-md border", src.cls)}>{src.text}</span>
                    {!a.is_active && (
                      <span className="text-[9.5px] font-bold uppercase px-1.5 py-0.5 rounded-md border bg-gray-100 text-gray-500 border-gray-200">
                        выключена
                      </span>
                    )}
                  </div>
                  <p className="text-[12px] font-medium text-gray-500 truncate mt-0.5">
                    <span title={a.schedule} className="font-bold text-gray-700">{a.schedule_label}</span>
                    {a.next_run && a.is_active && <> · следующий: {a.next_run}</>}
                    {a.creator_label && <> · поставил: {a.creator_label}</>}
                    {(a.description || a.prompt) && <> · {a.description || a.prompt}</>}
                  </p>
                </div>
                <span className={cn("text-[10.5px] font-bold px-2 py-1 rounded-md border shrink-0 whitespace-nowrap", st.cls)} title={a.last_error || a.last_result}>
                  {st.text}
                </span>
                {!system && (
                  <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() =>
                        void act(a.id, () =>
                          a.kind === "task" ? runRecurringTask(a.recurring_id!) : runAgentAutomation(a.id),
                        )
                      }
                      disabled={busyId !== null || a.last_status === "running"}
                      title={a.kind === "task" ? "Создать задачу сейчас (проверка)" : "Запустить сейчас (проверка)"}
                      className="p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg transition-colors disabled:opacity-40"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() =>
                        void act(a.id, () =>
                          a.kind === "task"
                            ? updateRecurringTask(a.recurring_id!, { is_active: !a.is_active })
                            : updateAgentAutomation(a.id, { is_active: !a.is_active }),
                        )
                      }
                      disabled={busyId !== null}
                      title={a.is_active ? "Выключить" : "Включить"}
                      className={cn(
                        "px-2 py-1 text-[10px] font-bold uppercase rounded-md border transition-colors",
                        a.is_active
                          ? "bg-emerald-50 border-emerald-100 text-emerald-600 hover:bg-emerald-100"
                          : "bg-gray-100 border-gray-200 text-gray-500 hover:bg-gray-200",
                      )}
                    >
                      {a.is_active ? "вкл" : "выкл"}
                    </button>
                    <button
                      onClick={() => {
                        const label = a.kind === "task" ? "регулярную задачу" : "автоматизацию";
                        if (window.confirm(`Удалить ${label} «${a.name}»?`)) {
                          void act(a.id, () =>
                            a.kind === "task" ? deleteRecurringTask(a.recurring_id!) : deleteAgentAutomation(a.id),
                          );
                        }
                      }}
                      disabled={busyId !== null}
                      title="Удалить"
                      className="p-1.5 text-gray-300 hover:text-rose-500 hover:bg-rose-50 rounded-lg transition-colors disabled:opacity-40"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
              {open && (
                <div className="px-4 pb-4 pt-1 border-t border-gray-100 space-y-2 text-[12.5px] font-medium text-gray-600">
                  {system ? (
                    <p>
                      Живёт в Hermes cron на сервере ({a.creator_label}) — приложение показывает её как витрину,
                      управление через владельца.
                    </p>
                  ) : (
                    <>
                      {a.prompt && (
                        <p className="whitespace-pre-wrap break-words">
                          <span className="font-bold text-gray-800">Задача:</span> {a.prompt}
                        </p>
                      )}
                      {a.kind === "task" ? (
                        <p>
                          Задачу в Bitrix создаёт планировщик приложения точно по расписанию — без хода
                          агента (не тратит лимиты).
                        </p>
                      ) : (
                      <p>
                        <span className="font-bold text-gray-800">Доставка:</span>{" "}
                        {a.deliver_to || "чат уведомлений Albery"}
                      </p>
                      )}
                      {a.last_error && <p className="text-rose-600 break-words">Последняя ошибка: {a.last_error}</p>}
                      {a.last_result && (
                        <p className="whitespace-pre-wrap break-words bg-slate-50 border border-gray-100 rounded-xl p-3 text-gray-600">
                          {a.last_result}
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {loaded && rows.length === 0 && (
          <div className="text-center text-gray-400 text-[13px] font-medium py-10">
            У этого агента пока нет автоматизаций — добавьте здесь или попросите агента в чате
            («присылай мне каждый день в 9:00 сводку…»).
          </div>
        )}
        {!loaded && !error && (
          <div className="text-center text-gray-400 text-[13px] font-medium py-10">Загрузка…</div>
        )}
      </div>
    </div>
  );
};

export default AutomationsPanel;
