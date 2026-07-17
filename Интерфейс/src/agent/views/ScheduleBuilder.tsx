import React, { useMemo, useState } from "react";
import { cn } from "../../lib/utils";

// Human-friendly schedule builder: turns a 5-field cron into plain controls and back, so
// nobody types «0 9 22 1 *» by hand. Four shapes cover every automation on this portal:
//   • по дням недели + время   («0 9 * * 1-5»)
//   • каждые N минут/часов     («*/5 * * * *», «0 */2 * * *»)
//   • раз в год: день + месяц  («0 9 22 1 *» — дни рождения)
//   • сложное расписание (cron) — единственный ручной путь, для всего необычного
// Whatever the mode, the value handed to the caller is always a valid 5-field cron string.

export type SchedMode = "weekly" | "interval" | "annual" | "cron";

const DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]; // index+1 = Mon=1..Sun=7
const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

const pad = (n: number) => String(n).padStart(2, "0");

type Parsed =
  | { mode: "weekly"; time: string; days: number[] }
  | { mode: "interval"; every: number; unit: "minutes" | "hours" }
  | { mode: "annual"; time: string; day: number; month: number }
  | { mode: "cron"; cron: string };

export const parseCron = (cronRaw: string): Parsed => {
  const cron = (cronRaw || "").trim();
  const f = cron.split(/\s+/);
  const raw: Parsed = { mode: "cron", cron };
  if (f.length !== 5) return raw;
  const [mm, hh, dom, mon, dow] = f;

  // Interval: every N minutes / hours.
  if (/^\*\/\d{1,2}$/.test(mm) && hh === "*" && dom === "*" && mon === "*" && dow === "*") {
    return { mode: "interval", every: Number(mm.slice(2)), unit: "minutes" };
  }
  if (mm === "0" && /^\*\/\d{1,2}$/.test(hh) && dom === "*" && mon === "*" && dow === "*") {
    return { mode: "interval", every: Number(hh.slice(2)), unit: "hours" };
  }
  if (mm === "0" && hh === "*" && dom === "*" && mon === "*" && dow === "*") {
    return { mode: "interval", every: 1, unit: "hours" };
  }

  const timeOk = /^\d{1,2}$/.test(mm) && /^\d{1,2}$/.test(hh) && Number(mm) < 60 && Number(hh) < 24;

  // Annual: fixed day + month, any year (birthday reminders).
  if (timeOk && /^\d{1,2}$/.test(dom) && /^\d{1,2}$/.test(mon) && dow === "*"
      && Number(dom) >= 1 && Number(dom) <= 31 && Number(mon) >= 1 && Number(mon) <= 12) {
    return { mode: "annual", time: `${pad(Number(hh))}:${pad(Number(mm))}`, day: Number(dom), month: Number(mon) };
  }

  // Weekly: fixed time on a set of weekdays (ranges/lists allowed).
  if (timeOk && dom === "*" && mon === "*") {
    let days: number[];
    if (dow === "*") {
      days = [1, 2, 3, 4, 5, 6, 7];
    } else {
      days = [];
      let good = true;
      for (const part of dow.split(",")) {
        const m = part.match(/^(\d)(?:-(\d))?$/);
        if (!m) { good = false; break; }
        const a = Number(m[1]);
        const b = m[2] !== undefined ? Number(m[2]) : a;
        if (a > 7 || b > 7 || a > b) { good = false; break; }
        for (let d = a; d <= b; d++) {
          const day = d === 0 ? 7 : d; // vixie: 0 and 7 are both Sunday
          if (!days.includes(day)) days.push(day);
        }
      }
      if (!good) return raw;
      days.sort((x, y) => x - y);
    }
    return { mode: "weekly", time: `${pad(Number(hh))}:${pad(Number(mm))}`, days };
  }

  return raw;
};

const buildWeekly = (time: string, days: number[]): string => {
  const [hh, mm] = time.split(":").map(Number);
  const dow = days.length === 7 ? "*" : [...days].sort((a, b) => a - b).map((d) => (d === 7 ? 0 : d)).join(",");
  return `${mm} ${hh} * * ${dow}`;
};

const buildInterval = (every: number, unit: "minutes" | "hours"): string =>
  unit === "minutes" ? `*/${every} * * * *` : every === 1 ? "0 * * * *" : `0 */${every} * * *`;

const buildAnnual = (time: string, day: number, month: number): string => {
  const [hh, mm] = time.split(":").map(Number);
  return `${mm} ${hh} ${day} ${month} *`;
};

const timeOk = (t: string) => /^\d{1,2}:\d{2}$/.test(t) && Number(t.split(":")[0]) < 24 && Number(t.split(":")[1]) < 60;
const cronOk = (c: string) => c.trim().split(/\s+/).length === 5;

const MODE_TABS: { id: SchedMode; label: string }[] = [
  { id: "weekly", label: "По дням недели" },
  { id: "interval", label: "Каждые N" },
  { id: "annual", label: "Раз в год" },
  { id: "cron", label: "Сложное (cron)" },
];

/** Human-readable summary of any cron for confident saving. */
export const describeCron = (cron: string): string => {
  const p = parseCron(cron);
  if (p.mode === "interval") return p.unit === "minutes" ? `каждые ${p.every} мин` : p.every === 1 ? "каждый час" : `каждые ${p.every} ч`;
  if (p.mode === "annual") return `ежегодно ${p.day} ${MONTHS[p.month - 1]} в ${p.time}`;
  if (p.mode === "weekly") {
    if (p.days.length === 7) return `каждый день в ${p.time}`;
    if (p.days.length === 5 && p.days.every((d) => d <= 5)) return `по будням в ${p.time}`;
    if (p.days.length === 2 && p.days[0] === 6 && p.days[1] === 7) return `по выходным в ${p.time}`;
    return `${p.days.map((d) => DAY_LABELS[d - 1]).join(", ")} в ${p.time}`;
  }
  return `cron: ${cron}`;
};

export const ScheduleBuilder: React.FC<{
  value: string;
  onChange: (cron: string, valid: boolean) => void;
}> = ({ value, onChange }) => {
  const initial = useMemo(() => parseCron(value), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [mode, setMode] = useState<SchedMode>(initial.mode);
  const [days, setDays] = useState<number[]>(initial.mode === "weekly" ? initial.days : [1, 2, 3, 4, 5]);
  const [time, setTime] = useState<string>(
    initial.mode === "weekly" || initial.mode === "annual" ? initial.time : "09:00",
  );
  const [every, setEvery] = useState<number>(initial.mode === "interval" ? initial.every : 30);
  const [unit, setUnit] = useState<"minutes" | "hours">(initial.mode === "interval" ? initial.unit : "minutes");
  const [day, setDay] = useState<number>(initial.mode === "annual" ? initial.day : 1);
  const [month, setMonth] = useState<number>(initial.mode === "annual" ? initial.month : 1);
  const [rawCron, setRawCron] = useState<string>(value);

  // Recompute the cron whenever any control changes and lift it to the parent.
  const emit = (next: Partial<{
    mode: SchedMode; days: number[]; time: string; every: number; unit: "minutes" | "hours"; day: number; month: number; rawCron: string;
  }>) => {
    const m = next.mode ?? mode;
    const d = next.days ?? days;
    const t = next.time ?? time;
    const e = next.every ?? every;
    const u = next.unit ?? unit;
    const dd = next.day ?? day;
    const mo = next.month ?? month;
    const rc = next.rawCron ?? rawCron;
    if (m === "weekly") return onChange(buildWeekly(t, d), timeOk(t) && d.length > 0);
    if (m === "interval") return onChange(buildInterval(e, u), e >= 1);
    if (m === "annual") return onChange(buildAnnual(t, dd, mo), timeOk(t));
    return onChange(rc.trim(), cronOk(rc));
  };

  const pickMode = (m: SchedMode) => { setMode(m); emit({ mode: m }); };
  const toggleDay = (d: number) => {
    const nextDays = days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort((a, b) => a - b);
    setDays(nextDays); emit({ days: nextDays });
  };

  return (
    <div className="space-y-3">
      {/* Mode segmented control */}
      <div className="inline-flex flex-wrap gap-1 bg-slate-100 rounded-xl p-1">
        {MODE_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => pickMode(t.id)}
            className={cn(
              "px-3 py-1.5 rounded-lg text-[12px] font-bold transition-colors",
              mode === t.id ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500 hover:text-slate-700",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {mode === "weekly" && (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-1">
            {DAY_LABELS.map((label, i) => {
              const d = i + 1;
              const on = days.includes(d);
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleDay(d)}
                  className={cn(
                    "px-2.5 py-1.5 rounded-lg text-[11.5px] font-bold border transition-colors",
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
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium text-gray-500">в</span>
            <input
              type="time"
              value={time}
              onChange={(e) => { setTime(e.target.value); emit({ time: e.target.value }); }}
              className="px-2.5 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold outline-none focus:border-indigo-500"
            />
            <span className="text-[11.5px] text-gray-400">МСК</span>
          </div>
        </div>
      )}

      {mode === "interval" && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[12px] font-medium text-gray-500">каждые</span>
          <input
            type="number"
            min={1}
            max={unit === "minutes" ? 59 : 23}
            value={every}
            onChange={(e) => { const v = Math.max(1, Number(e.target.value) || 1); setEvery(v); emit({ every: v }); }}
            className="w-16 px-2.5 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold text-center outline-none focus:border-indigo-500"
          />
          <select
            value={unit}
            onChange={(e) => { const u = e.target.value as "minutes" | "hours"; setUnit(u); emit({ unit: u }); }}
            className="px-3 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold text-gray-700 outline-none focus:border-indigo-500"
          >
            <option value="minutes">минут</option>
            <option value="hours">часов</option>
          </select>
          <span className="text-[11.5px] text-gray-400">круглосуточно</span>
        </div>
      )}

      {mode === "annual" && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[12px] font-medium text-gray-500">каждый год</span>
          <input
            type="number"
            min={1}
            max={31}
            value={day}
            onChange={(e) => { const v = Math.min(31, Math.max(1, Number(e.target.value) || 1)); setDay(v); emit({ day: v }); }}
            className="w-16 px-2.5 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold text-center outline-none focus:border-indigo-500"
          />
          <select
            value={month}
            onChange={(e) => { const v = Number(e.target.value); setMonth(v); emit({ month: v }); }}
            className="px-3 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold text-gray-700 outline-none focus:border-indigo-500"
          >
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
          <span className="text-[12px] font-medium text-gray-500">в</span>
          <input
            type="time"
            value={time}
            onChange={(e) => { setTime(e.target.value); emit({ time: e.target.value }); }}
            className="px-2.5 py-1.5 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-bold outline-none focus:border-indigo-500"
          />
          <span className="text-[11.5px] text-gray-400">МСК</span>
        </div>
      )}

      {mode === "cron" && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="text"
            value={rawCron}
            onChange={(e) => { setRawCron(e.target.value); emit({ rawCron: e.target.value }); }}
            placeholder="0 9 * * 1-5"
            title="5 полей cron: минуты часы день-месяца месяц день-недели (время МСК)"
            className="w-44 px-3 py-2 bg-white border border-gray-200/80 rounded-lg text-[12.5px] font-mono font-bold outline-none focus:border-indigo-500"
          />
          <span className="text-[11.5px] text-gray-400">минуты часы день месяц день-недели · МСК</span>
        </div>
      )}

      <p className="text-[11.5px] font-medium text-gray-400">
        Итог: <span className="font-bold text-indigo-500">{describeCron(
          mode === "weekly" ? buildWeekly(time, days)
            : mode === "interval" ? buildInterval(every, unit)
            : mode === "annual" ? buildAnnual(time, day, month)
            : rawCron,
        )}</span>
      </p>
    </div>
  );
};

export default ScheduleBuilder;
