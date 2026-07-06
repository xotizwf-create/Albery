import { useCallback, useEffect, useState } from "react";
import {
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Clock,
  Zap,
  BookOpen,
  Crown,
  Package,
  LayoutGrid,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  Tooltip,
  ResponsiveContainer,
  YAxis,
  CartesianGrid,
} from "recharts";
import { cn } from "../../lib/utils";
import { MonitoringData, fetchAgents, fetchMonitoring } from "../api";
import { AgentConfig } from "../types";

const CARD_TONE: Record<string, string> = {
  good: "text-emerald-500",
  bad: "text-rose-500",
  muted: "text-gray-400",
};

const REFRESH_MS = 30000;

const ALL_AGENTS_ID = "all";

export function MonitoringView() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [activeAgentId, setActiveAgentId] = useState(ALL_AGENTS_ID);
  const [data, setData] = useState<MonitoringData | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [chartDays, setChartDays] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchAgents()
      .then(setAgents)
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    setRefreshing(true);
    fetchMonitoring(chartDays, activeAgentId)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [chartDays, activeAgentId]);

  // Switching the agent must never show the previous agent's numbers, even for a moment.
  useEffect(() => {
    setData(null);
  }, [activeAgentId]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const isAll = activeAgentId === ALL_AGENTS_ID;
  const activeAgent = isAll ? undefined : agents.find((a) => a.id === activeAgentId);
  const online = !!data && !error;

  return (
    <div className="flex flex-col lg:flex-row items-start gap-6 h-[calc(100vh-14rem)] min-h-[560px]">
      {/* Left Sidebar - Agents List */}
      <div className="w-full lg:w-[320px] xl:w-[340px] shrink-0 flex flex-col h-full bg-white rounded-3xl border border-gray-200/60 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-100 bg-slate-50/50 h-[72px] flex items-center shrink-0">
          <h2 className="font-bold text-gray-900 text-[15px] px-2">
            Агенты для мониторинга
          </h2>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          <div
            onClick={() => setActiveAgentId(ALL_AGENTS_ID)}
            className={cn(
              "p-4 rounded-2xl cursor-pointer transition-all group relative",
              isAll ? "bg-gray-50" : "bg-transparent hover:bg-gray-50/50",
            )}
          >
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm bg-indigo-100 text-indigo-500">
                <LayoutGrid className="w-5 h-5" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-bold text-gray-900 text-[15px] truncate mb-0.5">
                  Все агенты
                </h3>
                <p className="text-[12.5px] text-gray-500 font-medium truncate">
                  сводка по всем ботам вместе
                </p>
              </div>
            </div>
          </div>
          {agents
            .filter((a) => a.isActive)
            .map((agent) => {
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

      {/* Right Content - Monitoring */}
      <div className="flex-1 min-w-0 h-full overflow-y-auto pr-2 space-y-6 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full pb-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 mb-1">
              Мониторинг: {isAll ? "Все агенты" : activeAgent?.name || "…"}
            </h1>
            <p className="text-gray-500 text-sm">
              {isAll
                ? "Сводные показатели всех агентов — живые данные, обновление каждые 30 сек"
                : "Показатели только этого агента (ходы, скорость, ошибки) — живые данные, обновление каждые 30 сек"}
            </p>
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

        {error && (
          <div className="flex items-center gap-2 p-4 rounded-2xl bg-rose-50 border border-rose-100 text-rose-600 text-[13px] font-bold">
            <AlertTriangle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        {/* Status Bar */}
        <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              <div className="relative flex h-3 w-3">
                {online && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                )}
                <span
                  className={cn(
                    "relative inline-flex rounded-full h-3 w-3",
                    online ? "bg-emerald-500" : "bg-gray-300",
                  )}
                ></span>
              </div>
              <span className="font-bold text-gray-900">
                {online ? (isAll ? "Все агенты в строю" : "Агент в строю") : "Нет данных"}
              </span>
            </div>
            {data && (
              <>
                <span className="px-2.5 py-1 bg-emerald-50 text-emerald-600 text-xs font-bold rounded-md">
                  аптайм {data.status.uptime}
                </span>
                <span className="text-sm font-medium text-gray-500 bg-gray-50 px-3 py-1 rounded-md">
                  последний ход — {data.status.last_turn}
                </span>
                {data.status.slots_total !== null && (
                  <span className="text-sm font-medium text-gray-500 bg-gray-50 px-3 py-1 rounded-md">
                    очередь {data.status.slots_busy} из {data.status.slots_total}
                  </span>
                )}
              </>
            )}
          </div>
          <div className="text-sm font-mono text-gray-400">
            прод 186 {data?.status.version ? `• ${data.status.version}` : ""}
          </div>
        </div>

        {/* Metrics Cards */}
        <div className="grid grid-cols-4 gap-4">
          {(data?.cards || []).map((stat, i) => (
            <div
              key={i}
              className="bg-white p-5 rounded-2xl border border-gray-100 shadow-sm flex flex-col"
            >
              <span className="text-sm font-medium text-gray-500 mb-2">
                {stat.label}
              </span>
              <span className="text-3xl font-bold text-gray-900 mb-2">
                {stat.value}
              </span>
              <span
                className={cn("text-xs font-semibold mt-auto", CARD_TONE[stat.tone] || CARD_TONE.muted)}
              >
                {stat.sub}
              </span>
            </div>
          ))}
          {!data &&
            [0, 1, 2, 3].map((i) => (
              <div key={i} className="bg-white p-5 rounded-2xl border border-gray-100 shadow-sm h-[110px] animate-pulse" />
            ))}
        </div>

        {/* Charts & Health Row */}
        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-white p-6 rounded-2xl border border-gray-100 shadow-sm">
            <div className="flex items-center gap-2 mb-6 flex-wrap">
              <Clock className="w-4 h-4 text-gray-400" />
              <h3 className="font-semibold text-gray-900">
                Скорость ответов, сек — каждый ход
              </h3>
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[11px] font-bold text-gray-400 mr-1">
                  {data?.chart.length ?? 0} ходов
                </span>
                {[
                  { d: 1, label: "24 часа" },
                  { d: 3, label: "3 дня" },
                  { d: 7, label: "7 дней" },
                  { d: 30, label: "30 дней" },
                ].map((p) => (
                  <button
                    key={p.d}
                    onClick={() => setChartDays(p.d)}
                    className={cn(
                      "px-2.5 py-1 rounded-lg text-[11.5px] font-bold transition-colors",
                      chartDays === p.d
                        ? "bg-indigo-50 text-indigo-700"
                        : "bg-gray-50 text-gray-500 hover:bg-gray-100",
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="h-[200px] w-full focus:outline-none">
              <ResponsiveContainer
                width="100%"
                height="100%"
                className="focus:outline-none [&_*]:focus:outline-none"
              >
                <LineChart
                  data={data?.chart || []}
                  margin={{ top: 5, right: 10, left: -20, bottom: 0 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    vertical={false}
                    stroke="#f1f5f9"
                  />
                  <XAxis
                    dataKey="time"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                    dy={10}
                    interval={Math.max(0, Math.ceil((data?.chart.length ?? 0) / 10) - 1)}
                  />
                  <YAxis
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                  />
                  <Tooltip
                    formatter={(value: any, _name: any, entry: any) => [
                      `${value} сек${entry?.payload?.error ? " • ход с ошибкой" : ""}`,
                      "скорость",
                    ]}
                    contentStyle={{
                      borderRadius: "12px",
                      border: "none",
                      boxShadow:
                        "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
                    }}
                    labelStyle={{
                      color: "#64748b",
                      fontSize: "12px",
                      marginBottom: "4px",
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="speed"
                    stroke="#4f46e5"
                    strokeWidth={2.5}
                    connectNulls
                    dot={(props: any) => {
                      const { cx, cy, payload, index } = props;
                      if (payload.error || payload.speed > 120) {
                        return (
                          <circle
                            key={`dot-${index}`}
                            cx={cx}
                            cy={cy}
                            r={4}
                            fill="#ef4444"
                            stroke="none"
                          />
                        );
                      }
                      return <circle key={`dot-${index}`} cx={cx} cy={cy} r={2} fill="#a5b4fc" stroke="none" />;
                    }}
                    activeDot={{
                      r: 6,
                      fill: "#4f46e5",
                      stroke: "#fff",
                      strokeWidth: 2,
                    }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-white p-6 rounded-2xl border border-gray-100 shadow-sm flex flex-col">
            <div className="flex items-center gap-2 mb-6">
              <Zap className="w-4 h-4 text-gray-400" />
              <h3 className="font-semibold text-gray-900">Здоровье систем</h3>
              {!isAll && (
                <span className="text-[10.5px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-md">
                  общие для всех
                </span>
              )}
            </div>
            <div className="space-y-5 flex-1">
              {(data?.health || []).map((sys, i) => (
                <div key={i} className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div
                      className={cn(
                        "w-2 h-2 rounded-full",
                        sys.type === "ok" ? "bg-emerald-500" : "bg-orange-500",
                      )}
                    />
                    <span className="text-sm font-medium text-gray-700">
                      {sys.label}
                    </span>
                  </div>
                  <span className="text-xs font-medium text-gray-400">
                    {sys.status}
                  </span>
                </div>
              ))}
              {!data && (
                <div className="text-center text-gray-400 text-[13px] font-medium py-6">
                  Загрузка…
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Events Feed */}
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-50 flex items-center justify-between bg-gray-50/50">
            <div className="flex items-center gap-2">
              <span className="text-lg">📜</span>
              <h3 className="font-semibold text-gray-900">Лента событий</h3>
            </div>
            <span className="px-3 py-1 bg-white border border-gray-200 rounded-lg text-xs font-medium text-gray-500 shadow-sm">
              автообновление 30 сек
            </span>
          </div>
          <div className="p-2">
            {(data?.events || []).map((event, i) => (
              <div
                key={i}
                className="flex items-start gap-4 p-3 hover:bg-gray-50 rounded-xl transition-colors"
              >
                <span className="text-xs font-medium text-gray-400 w-16 pt-0.5 shrink-0">
                  {event.time}
                </span>
                <div className="mt-0.5 shrink-0">
                  {event.type === "success" && (
                    <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                  )}
                  {event.type === "deploy" && (
                    <span className="text-base leading-none">🚀</span>
                  )}
                  {event.type === "error" && (
                    <AlertTriangle className="w-4 h-4 text-rose-500" />
                  )}
                  {event.type === "report" && (
                    <UserIcon className="w-4 h-4 text-indigo-500" />
                  )}
                  {event.type === "info" && (
                    <AlertCircle className="w-4 h-4 text-orange-400" />
                  )}
                </div>
                <div className="flex-1 text-sm text-gray-700 leading-relaxed font-medium break-words [overflow-wrap:anywhere]">
                  {event.text}
                </div>
              </div>
            ))}
            {!data && (
              <div className="text-center text-gray-400 text-[13px] font-medium py-8">
                Загрузка событий…
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function UserIcon(props: any) {
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
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}
