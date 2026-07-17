import React, { useEffect, useState } from "react";
import { Clock, Play, Plus, Server, Trash2 } from "lucide-react";
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
import { ScheduleBuilder } from "./ScheduleBuilder";
import { cn } from "../../lib/utils";

// Sub-tab «Автоматизации» of the agent editor: the agent's own cron jobs. System rows
// mirror the legacy Hermes crons (read-only); agent rows are created by the owner here
// or by the agent itself from chat (schedule_my_automation) and run in-app.

const statusChip = (a: AgentAutomation): { text: string; cls: string } => {
  if (!a.last_status) return { text: "ещё не запускалась", cls: "bg-gray-100 text-gray-500 border-gray-200" };
  if (a.last_status === "running") return { text: "выполняется…", cls: "bg-sky-50 text-sky-600 border-sky-100 animate-pulse" };
  if (a.last_status === "interrupted") return { text: `прервана · ${a.last_run}`, cls: "bg-amber-50 text-amber-600 border-amber-100" };
  if (a.last_status === "skipped") return { text: `пропущена · ${a.last_run}`, cls: "bg-amber-50 text-amber-600 border-amber-100" };
  if (a.last_status === "error") return { text: `ошибка · ${a.last_run}`, cls: "bg-rose-50 text-rose-600 border-rose-100" };
  if (a.last_status === "silent") return { text: `тихо · ${a.last_run}`, cls: "bg-gray-100 text-gray-500 border-gray-200" };
  return { text: `ок · ${a.last_run}`, cls: "bg-emerald-50 text-emerald-600 border-emerald-100" };
};

// --- Schedule editor -------------------------------------------------------------------------
// task rows edit weekdays/create_time in the recurring registry (a monthly row edits time only);
// every other row (agent cron + editable system) uses the human ScheduleBuilder, which round-trips
// any 5-field cron — weekly / interval / annual / raw — so «0 9 22 1 *» is never typed by hand.

const DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]; // index+1 = Mon=1..Sun=7

const ScheduleEditor: React.FC<{
  row: AgentAutomation;
  disabled: boolean;
  onSave: (payload: { weekdays?: number[]; create_time?: string; schedule?: string }) => void;
}> = ({ row, disabled, onSave }) => {
  const isTask = row.kind === "task";
  const monthly = isTask && row.period === "monthly";
  const [days, setDays] = useState<number[]>(isTask ? row.weekdays || [] : []);
  const [time, setTime] = useState<string>(isTask ? row.create_time || "09:00" : "09:00");
  const [cron, setCron] = useState<string>(row.schedule);
  const [cronValid, setCronValid] = useState(true);

  const toggleDay = (d: number) =>
    setDays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b)));

  const timeOk = /^\d{1,2}:\d{2}$/.test(time) && Number(time.split(":")[0]) < 24 && Number(time.split(":")[1]) < 60;
  const canSave = isTask ? timeOk && (monthly || days.length > 0) : cronValid;

  const save = () => {
    if (isTask) return onSave(monthly ? { create_time: time } : { weekdays: days, create_time: time });
    return onSave({ schedule: cron.trim() });
  };

  return (
    <div className="bg-slate-50/70 border border-gray-100 rounded-xl p-3 space-y-3">
      <p className="text-[11px] font-bold uppercase text-gray-400">Изменить расписание</p>
      {isTask ? (
        <div className="flex items-center gap-3 flex-wrap">
          {!monthly && (
            <div className="flex items-center gap-1">
              {DAY_LABELS.map((label, i) => {
                const d = i + 1;
                const on = days.includes(d);
                return (
                  <button
                    key={d}
                    onClick={() => toggleDay(d)}
                    className={cn(
                      "px-2 py-1.5 rounded-lg text-[11.5px] font-bold border transition-colors",
                      on
                        ? "bg-indigo-600 border-indigo-600 text-white"
                        : "bg-white border-gray-200 text-gray-400 hover:border-indigo-300 hover:text-indigo-500",
                      d >= 6 && !on && "text-rose-300",
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          )}
          {monthly && <span className="text-[11.5px] text-gray-400">ежемесячный повтор — меняется только время</span>}
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="px-2.5 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold outline-none focus:border-indigo-500"
          />
          <span className="text-[11.5px] text-gray-400">МСК</span>
        </div>
      ) : (
        <ScheduleBuilder value={row.schedule} onChange={(c, v) => { setCron(c); setCronValid(v); }} />
      )}
      <button
        onClick={save}
        disabled={disabled || !canSave}
        className="px-4 py-1.5 bg-indigo-600 text-white rounded-lg text-[12px] font-bold hover:bg-indigo-700 transition-all disabled:opacity-40"
      >
        Сохранить расписание
      </button>
    </div>
  );
};

// Exactly three types, nothing else: a recurring Bitrix task, a cron automation (agent run on
// schedule — whether the owner or the agent set it up), or a built-in system automation (Hermes cron).
const sourceChip = (a: AgentAutomation): { text: string; cls: string } => {
  if (a.kind === "task") return { text: "регулярная задача", cls: "bg-sky-50 text-sky-600 border-sky-100" };
  if (a.kind === "system") return { text: "системная автоматизация", cls: "bg-violet-50 text-violet-600 border-violet-100" };
  return { text: "крон автоматизация", cls: "bg-indigo-50 text-indigo-600 border-indigo-100" };
};

export const AutomationsPanel: React.FC<{ slug: string }> = ({ slug }) => {
  const [rows, setRows] = useState<AgentAutomation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | 0 | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [showForm, setShowForm] = useState(false);
  // Filter the list by WHO created each automation (owner / an employee who asked / Hermes).
  const [creatorFilter, setCreatorFilter] = useState<string>("all");
  const [fName, setFName] = useState("");
  const [fSchedule, setFSchedule] = useState("0 9 * * 1-5");
  const [fScheduleValid, setFScheduleValid] = useState(true);
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

  // Distinct creators present, for the filter dropdown; «Все» keeps the full list.
  const creators = Array.from(new Set<string>(rows.map((a) => a.creator).filter(Boolean))).sort(
    (a, b) => a.localeCompare(b, "ru"),
  );
  const filteredRows =
    creatorFilter === "all" ? rows : rows.filter((a) => a.creator === creatorFilter);
  const nTasks = filteredRows.filter((a) => a.kind === "task").length;
  const nSystem = filteredRows.filter((a) => a.kind === "system").length;
  const nCron = filteredRows.length - nTasks - nSystem;

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
          <input
            type="text"
            value={fName}
            onChange={(e) => setFName(e.target.value)}
            placeholder="Название (например «Утренняя сводка задач»)"
            className="w-full px-3.5 py-2.5 bg-white border border-gray-200/80 rounded-xl text-[13px] font-bold focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none shadow-sm"
          />
          <div className="bg-white border border-gray-200/80 rounded-xl p-3.5">
            <p className="text-[11px] font-bold uppercase text-gray-400 mb-2">Когда запускать</p>
            <ScheduleBuilder value={fSchedule} onChange={(c, v) => { setFSchedule(c); setFScheduleValid(v); }} />
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
              disabled={busyId !== null || !fName.trim() || !fPrompt.trim() || !fScheduleValid}
              className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-[13px] font-bold hover:bg-indigo-700 shadow-sm transition-all disabled:opacity-50"
            >
              Создать
            </button>
          </div>
        </div>
      )}

      {error && <div className="text-[13px] font-bold text-rose-500">{error}</div>}

      {loaded && creators.length > 0 && (
        <div className="flex items-center gap-3 flex-wrap bg-white border border-gray-200/70 rounded-xl px-3.5 py-2.5 shadow-sm">
          <span className="text-[12px] font-bold text-gray-500">Создатель:</span>
          <select
            value={creatorFilter}
            onChange={(e) => {
              setCreatorFilter(e.target.value);
              setOpenId(null);
            }}
            className="px-3 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold text-gray-700 outline-none focus:border-indigo-500 shadow-sm"
          >
            <option value="all">Все ({rows.length})</option>
            {creators.map((c) => (
              <option key={c} value={c}>
                {c} ({rows.filter((a) => a.creator === c).length})
              </option>
            ))}
          </select>
          <span className="text-[11.5px] font-medium text-gray-400">
            {creatorFilter === "all" ? "показаны все · " : `создал ${creatorFilter} · `}
            <span className="text-indigo-500 font-bold">{nCron}</span> крон-автоматизаций
            {" · "}
            <span className="text-sky-500 font-bold">{nTasks}</span> регулярных задач
            {nSystem > 0 && (
              <>
                {" · "}
                <span className="text-violet-500 font-bold">{nSystem}</span> системных
              </>
            )}
          </span>
          {creatorFilter !== "all" && (
            <button
              onClick={() => setCreatorFilter("all")}
              className="ml-auto text-[11.5px] font-bold text-gray-400 hover:text-indigo-600 transition-colors"
            >
              сбросить фильтр ✕
            </button>
          )}
        </div>
      )}

      <div className="space-y-2.5">
        {filteredRows.map((a) => {
          const st = statusChip(a);
          const src = sourceChip(a);
          const open = openId === a.id;
          const system = a.kind === "system";
          // Editable/runnable when not a system row, or a system row whose executor is known (057).
          const canEdit = !system || a.can_edit === true;
          const canRun = !system || a.can_run === true;
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
                    {a.creator && (
                      <>
                        {" · создал: "}
                        <span className="font-bold text-gray-700" title={a.creator_label}>{a.creator}</span>
                      </>
                    )}
                    {(a.description || a.prompt) && <> · {a.description || a.prompt}</>}
                  </p>
                </div>
                <span className={cn("text-[10.5px] font-bold px-2 py-1 rounded-md border shrink-0 whitespace-nowrap", st.cls)} title={a.last_error || a.last_result}>
                  {st.text}
                </span>
                {(canRun || canEdit || !system) && (
                  <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    {canRun && (
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
                    )}
                    {canEdit && (
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
                    )}
                    {!system && (
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
                    )}
                  </div>
                )}
              </div>
              {open && (
                <div className="px-4 pb-4 pt-1 border-t border-gray-100 space-y-2 text-[12.5px] font-medium text-gray-600">
                  {a.description && (
                    <p className="whitespace-pre-wrap break-words">
                      <span className="font-bold text-gray-800">Что делает:</span> {a.description}
                    </p>
                  )}
                  {system ? (
                    <>
                      <p className="flex items-center gap-1.5 text-gray-500">
                        <Server className="w-3.5 h-3.5" />
                        Выполняется на сервере ({a.creator_label || a.system_key || "системный процесс"})
                        {a.can_edit
                          ? " — расписанием и включением можно управлять отсюда."
                          : " — управление только на сервере."}
                      </p>
                      {a.last_run && <p className="text-gray-500">Последний запуск: {a.last_run} · {a.last_status || "—"}</p>}
                      {canEdit && (
                        <ScheduleEditor
                          key={`${a.id}:${a.schedule}`}
                          row={a}
                          disabled={busyId !== null}
                          onSave={(payload) => void act(a.id, () => updateAgentAutomation(a.id, { schedule: payload.schedule! }))}
                        />
                      )}
                      {a.last_error && <p className="text-rose-600 break-words">Последняя ошибка: {a.last_error}</p>}
                      {a.last_result && (
                        <p className="whitespace-pre-wrap break-words bg-slate-50 border border-gray-100 rounded-xl p-3 text-gray-600">
                          {a.last_result}
                        </p>
                      )}
                    </>
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
                      <ScheduleEditor
                        key={`${a.id}:${a.schedule}:${a.create_time}:${(a.weekdays || []).join("")}`}
                        row={a}
                        disabled={busyId !== null}
                        onSave={(payload) =>
                          void act(a.id, () =>
                            a.kind === "task"
                              ? updateRecurringTask(a.recurring_id!, payload)
                              : updateAgentAutomation(a.id, { schedule: payload.schedule! }),
                          )
                        }
                      />
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
        {loaded && rows.length > 0 && filteredRows.length === 0 && (
          <div className="text-center text-gray-400 text-[13px] font-medium py-10">
            У «{creatorFilter}» нет автоматизаций у этого агента.{" "}
            <button onClick={() => setCreatorFilter("all")} className="text-indigo-500 font-bold hover:underline">
              Показать все
            </button>
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
