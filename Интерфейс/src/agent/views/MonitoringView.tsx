import { useState } from "react";
import {
  RefreshCw,
  Play,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Clock,
  Zap,
  BookOpen,
  Crown,
  Package,
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
import { mockAgents } from "../data";

const chartData = [
  { time: "15:00", speed: 45 },
  { time: "16:00", speed: 40 },
  { time: "17:00", speed: 38 },
  { time: "18:00", speed: 42 },
  { time: "19:00", speed: 35 },
  { time: "20:00", speed: 48 },
  { time: "21:00", speed: 95 }, // The spike / timeout
  { time: "22:00", speed: 32 },
  { time: "23:00", speed: 28 },
  { time: "00:00", speed: 30 },
  { time: "сейчас", speed: 25 },
];

const events = [
  {
    id: 1,
    time: "15:00",
    type: "success",
    text: "Selfcheck: всё чисто, проблем не найдено",
  },
  {
    id: 2,
    time: "14:32",
    type: "deploy",
    text: "Деплой: живой статус агента (коммит 8e8e85c) — смоук пройден",
  },
  {
    id: 3,
    time: "11:04",
    type: "error",
    text: "Таймаут хода — диалог 22, мозг не ответил за 600с. Алерт отправлен в Telegram → исправлено в 14:20",
    resolved: true,
  },
  {
    id: 4,
    time: "10:15",
    type: "info",
    text: "Наталья Ким запросила расширение доступа → передано Александру",
  },
  {
    id: 5,
    time: "09:00",
    type: "success",
    text: "Утренняя проверка: Bitrix, Google, Zoom — все интеграции живы",
  },
];

export function MonitoringView() {
  const [activeAgentId, setActiveAgentId] = useState(mockAgents[0].id);
  const activeAgent =
    mockAgents.find((a) => a.id === activeAgentId) || mockAgents[0];

  return (
    <div className="flex flex-col lg:flex-row items-start gap-6 h-[calc(100vh-8rem)] min-h-[700px]">
      {/* Left Sidebar - Agents List */}
      <div className="w-full lg:w-[320px] xl:w-[340px] shrink-0 flex flex-col h-full bg-white rounded-3xl border border-gray-200/60 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-100 bg-slate-50/50 h-[72px] flex items-center shrink-0">
          <h2 className="font-bold text-gray-900 text-[15px] px-2">
            Агенты для мониторинга
          </h2>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          {mockAgents
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
              Мониторинг: {activeAgent.name}
            </h1>
            <p className="text-gray-500 text-sm">
              Доступность агента, ошибки и скорость — всё в одном месте
            </p>
          </div>
          <div className="flex gap-3">
            <button className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-50 transition-all shadow-sm">
              <RefreshCw className="w-4 h-4 text-gray-400" />
              Прогнать проверку
            </button>
            <button className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-xl text-sm font-semibold hover:bg-indigo-700 transition-all shadow-sm shadow-indigo-600/20">
              <Play className="w-4 h-4 fill-current" />
              Перезапустить бота
            </button>
          </div>
        </div>

        {/* Status Bar */}
        <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
              </div>
              <span className="font-bold text-gray-900">Агент в строю</span>
            </div>
            <span className="px-2.5 py-1 bg-emerald-50 text-emerald-600 text-xs font-bold rounded-md">
              аптайм 14 дней
            </span>
            <span className="text-sm font-medium text-gray-500 bg-gray-50 px-3 py-1 rounded-md">
              последний ход — 2 мин назад
            </span>
            <span className="text-sm font-medium text-gray-500 bg-gray-50 px-3 py-1 rounded-md">
              очередь 0 из 3
            </span>
          </div>
          <div className="text-sm font-mono text-gray-400">
            прод 186 • v2026.07.02
          </div>
        </div>

        {/* Metrics Cards */}
        <div className="grid grid-cols-4 gap-4">
          {[
            {
              label: "Ходов сегодня",
              value: "47",
              sub: "▲ +18% к вчера",
              subColor: "text-emerald-500",
            },
            {
              label: "Средняя скорость",
              value: "36 сек",
              sub: "▼ быстрее на 9 сек",
              subColor: "text-emerald-500",
            },
            {
              label: "Ошибки за 24 часа",
              value: "1",
              sub: "таймаут • 11:03 • решено",
              subColor: "text-gray-400",
            },
            {
              label: "Лимиты мозга",
              value: "62%",
              sub: "■ обновятся в 19:00",
              subColor: "text-orange-500",
            },
          ].map((stat, i) => (
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
                className={cn("text-xs font-semibold mt-auto", stat.subColor)}
              >
                {stat.sub}
              </span>
            </div>
          ))}
        </div>

        {/* Charts & Health Row */}
        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 bg-white p-6 rounded-2xl border border-gray-100 shadow-sm">
            <div className="flex items-center gap-2 mb-6">
              <Clock className="w-4 h-4 text-gray-400" />
              <h3 className="font-semibold text-gray-900">
                Скорость ответов за 24 часа
              </h3>
            </div>
            <div className="h-[200px] w-full focus:outline-none">
              <ResponsiveContainer
                width="100%"
                height="100%"
                className="focus:outline-none [&_*]:focus:outline-none"
              >
                <LineChart
                  data={chartData}
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
                  />
                  <YAxis
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                  />
                  <Tooltip
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
                    strokeWidth={3}
                    dot={(props) => {
                      const { cx, cy, payload } = props;
                      if (payload.speed > 90) {
                        return (
                          <circle
                            cx={cx}
                            cy={cy}
                            r={4}
                            fill="#ef4444"
                            stroke="none"
                          />
                        );
                      }
                      return <circle cx={cx} cy={cy} r={0} />;
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
            </div>
            <div className="space-y-5 flex-1">
              {[
                {
                  label: "Мозг агента (ChatGPT)",
                  status: "отвечает • 1.2 с",
                  type: "ok",
                },
                {
                  label: "MCP-инструменты (145)",
                  status: "все живы",
                  type: "ok",
                },
                { label: "Bitrix REST", status: "ok • 0.4 с", type: "ok" },
                { label: "Google API", status: "ok • 0.7 с", type: "ok" },
                { label: "Память сервера", status: "1.1 / 2 ГБ", type: "warn" },
              ].map((sys, i) => (
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
              автообновление
            </span>
          </div>
          <div className="p-2">
            {events.map((event, i) => (
              <div
                key={event.id}
                className="flex items-start gap-4 p-3 hover:bg-gray-50 rounded-xl transition-colors"
              >
                <span className="text-xs font-medium text-gray-400 w-12 pt-0.5">
                  {event.time}
                </span>
                <div className="mt-0.5">
                  {event.type === "success" && (
                    <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                  )}
                  {event.type === "deploy" && (
                    <span className="text-base leading-none">🚀</span>
                  )}
                  {event.type === "error" && (
                    <AlertTriangle className="w-4 h-4 text-rose-500" />
                  )}
                  {event.type === "info" && (
                    <User className="w-4 h-4 text-indigo-500" />
                  )}
                </div>
                <div className="flex-1 text-sm text-gray-700 leading-relaxed font-medium">
                  {event.text.includes("8e8e85c") ? (
                    <>
                      Деплой: живой статус агента (коммит{" "}
                      <span className="font-bold text-gray-900 bg-gray-100 px-1 rounded">
                        8e8e85c
                      </span>
                      ) — смоук пройден
                    </>
                  ) : (
                    event.text
                  )}
                  {event.resolved && (
                    <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-50 text-emerald-600">
                      решено
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function User(props: any) {
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
