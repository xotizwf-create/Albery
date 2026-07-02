import { useCallback, useEffect, useState } from "react";
import { RefreshCw, AlertTriangle, Coins, Clock, MessageSquare, Users } from "lucide-react";
import { UsageData, fetchUsage } from "../api";
import { cn } from "../../lib/utils";

const PERIODS = [
  { id: "today", label: "Сегодня" },
  { id: "7", label: "7 дней" },
  { id: "30", label: "30 дней" },
  { id: "90", label: "90 дней" },
] as const;

const REFRESH_MS = 60000;

const fmtTokens = (n: number) =>
  n >= 1000000 ? `${(n / 1000000).toFixed(1)} млн` : n >= 1000 ? `${Math.round(n / 1000)} тыс` : String(n);

export function UsageView() {
  const [period, setPeriod] = useState<string>("7");
  const [data, setData] = useState<UsageData | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setRefreshing(true);
    fetchUsage(period)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [period]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const maxTokens = Math.max(1, ...(data?.rows || []).map((r) => r.tokens_est));

  const cards = data
    ? [
        { label: "Ходов агента", value: String(data.totals.turns), icon: MessageSquare, iconCls: "bg-indigo-50 text-indigo-500" },
        { label: "Время работы агента", value: data.totals.time_label, icon: Clock, iconCls: "bg-emerald-50 text-emerald-500" },
        { label: "≈ Токенов потрачено", value: fmtTokens(data.totals.tokens_est), icon: Coins, iconCls: "bg-amber-50 text-amber-500" },
        { label: "Активных сотрудников", value: String(data.totals.users), icon: Users, iconCls: "bg-sky-50 text-sky-500" },
      ]
    : [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">Использование агента</h1>
          <p className="text-gray-500 text-sm font-medium">
            Кто сколько работает с агентом: ходы, время и расход токенов за период
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex gap-2">
            {PERIODS.map((p) => (
              <button
                key={p.id}
                onClick={() => setPeriod(p.id)}
                className={cn(
                  "px-4 py-2 rounded-xl text-sm font-bold transition-colors",
                  period === p.id
                    ? "bg-indigo-50 text-indigo-700"
                    : "bg-white text-gray-600 hover:bg-gray-50 border border-gray-200 shadow-sm",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            onClick={load}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-50 transition-all shadow-sm disabled:opacity-60"
          >
            <RefreshCw className={cn("w-4 h-4 text-gray-400", refreshing && "animate-spin")} />
            Обновить
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-4 rounded-2xl bg-rose-50 border border-rose-100 text-rose-600 text-[13px] font-bold">
          <AlertTriangle className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((stat, i) => (
          <div key={i} className="bg-white p-5 rounded-2xl border border-gray-100 shadow-sm flex items-center gap-4">
            <div className={cn("w-11 h-11 rounded-xl flex items-center justify-center shrink-0", stat.iconCls)}>
              <stat.icon className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="text-[22px] font-bold text-gray-900 leading-tight truncate">{stat.value}</div>
              <div className="text-[12.5px] font-medium text-gray-500">{stat.label}</div>
            </div>
          </div>
        ))}
        {!data &&
          [0, 1, 2, 3].map((i) => (
            <div key={i} className="bg-white p-5 rounded-2xl border border-gray-100 shadow-sm h-[86px] animate-pulse" />
          ))}
      </div>

      {/* Per-employee table */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-50 flex items-center justify-between bg-gray-50/50">
          <h3 className="font-semibold text-gray-900">По сотрудникам</h3>
          <span className="px-3 py-1 bg-white border border-gray-200 rounded-lg text-xs font-medium text-gray-500 shadow-sm">
            автообновление 60 сек
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[11px] font-bold text-gray-400 uppercase tracking-wider border-b border-gray-50">
                <th className="px-5 py-3">Сотрудник</th>
                <th className="px-4 py-3 text-right">Ходов</th>
                <th className="px-4 py-3 text-right">Время работы</th>
                <th className="px-4 py-3 text-right">Ср. ход</th>
                <th className="px-4 py-3 text-right">Ошибки</th>
                <th className="px-5 py-3 w-[280px]">≈ Токены</th>
                <th className="px-4 py-3 text-right">Активность</th>
              </tr>
            </thead>
            <tbody>
              {(data?.rows || []).map((row) => (
                <tr key={row.bitrix_user_id ?? row.name} className="border-b border-gray-50 last:border-0 hover:bg-gray-50/60 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-700 flex items-center justify-center text-[10px] font-bold shrink-0">
                        {row.name
                          .split(/\s+/)
                          .filter(Boolean)
                          .slice(0, 2)
                          .map((n) => n[0])
                          .join("")}
                      </div>
                      <div className="min-w-0">
                        <div className="font-bold text-gray-900 text-[13.5px] truncate">{row.name}</div>
                        {row.position && (
                          <div className="text-[11.5px] font-medium text-gray-400 truncate">{row.position}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5 text-right font-bold text-gray-900 text-[13.5px]">{row.turns}</td>
                  <td className="px-4 py-3.5 text-right font-medium text-gray-600 text-[13.5px]">{row.time_label}</td>
                  <td className="px-4 py-3.5 text-right font-medium text-gray-500 text-[13px]">{row.avg_label}</td>
                  <td className="px-4 py-3.5 text-right">
                    {row.errors > 0 ? (
                      <span className="text-[12px] font-bold text-rose-600 bg-rose-50 border border-rose-100 px-2 py-0.5 rounded-md">
                        {row.errors}
                      </span>
                    ) : (
                      <span className="text-[12px] font-medium text-gray-300">0</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-indigo-400 to-indigo-600"
                          style={{ width: `${Math.max(3, Math.round((row.tokens_est / maxTokens) * 100))}%` }}
                        />
                      </div>
                      <span className="text-[12.5px] font-bold text-gray-700 w-16 text-right shrink-0">
                        {fmtTokens(row.tokens_est)}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3.5 text-right text-[12.5px] font-medium text-gray-400">{row.last_at}</td>
                </tr>
              ))}
              {data && data.rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-5 py-10 text-center text-gray-400 text-[13px] font-medium">
                    За выбранный период активности не было
                  </td>
                </tr>
              )}
              {!data && (
                <tr>
                  <td colSpan={7} className="px-5 py-10 text-center text-gray-400 text-[13px] font-medium">
                    Загрузка…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="px-5 py-3 border-t border-gray-50 bg-gray-50/40 text-[11.5px] font-medium text-gray-400">
          ≈ Токены — оценка по объёму переписки (~3 символа на токен). Точный учёт подключим, когда бот начнёт
          логировать usage из Hermes.
        </div>
      </div>
    </div>
  );
}
