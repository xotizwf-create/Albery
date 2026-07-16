import { useEffect, useMemo, useRef, useState } from "react";
import { AreaChart, Area, ResponsiveContainer } from 'recharts';
import { AlertTriangle, Info, LayoutDashboard } from "lucide-react";
import { cn } from "../lib/utils";
import { DatePicker, BrandSelect } from "./shared/FormControls";
import { RnpTab } from "./RnpTab";
import { SettingsContent } from "./SettingsContent";
import { api, qs, money, pct } from "../lib/api";

const TABS = ["Общий дашборд", "РНП", "ОПиУ", "ДДС", "По артикулам", "Налоговый калькулятор", "Настройка"];

const MOSCOW_DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
});

function moscowToday() {
  const parts = Object.fromEntries(MOSCOW_DATE.formatToParts(new Date()).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function monthsBefore(value: string, months: number) {
  const [year, month, day] = value.split('-').map(Number);
  const firstOfTarget = new Date(Date.UTC(year, month - 1 - months, 1, 12));
  const lastDay = new Date(Date.UTC(firstOfTarget.getUTCFullYear(), firstOfTarget.getUTCMonth() + 1, 0, 12)).getUTCDate();
  const date = new Date(Date.UTC(firstOfTarget.getUTCFullYear(), firstOfTarget.getUTCMonth(), Math.min(day, lastDay), 12));
  return date.toISOString().slice(0, 10);
}

function Sparkline({ data, color }: { data: { v: number }[], color: string }) {
  const gradientId = `color-${color.replace('#', '')}`;
  const safe = data && data.length > 1 ? data : [{ v: 0 }, { v: 0 }];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={safe}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={2} fillOpacity={1} fill={`url(#${gradientId})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function Row({ color, name, value, share, ring }: { color: string, name: string, value: string, share?: string, ring?: boolean }) {
  return (
    <div className="flex justify-between items-center">
      <div className="flex items-center gap-2.5 min-w-0">
        <div className={cn("w-2 h-2 rounded-full shrink-0", ring && "ring-4")} style={{ backgroundColor: color, boxShadow: ring ? `0 0 0 4px ${color}1a` : undefined }}></div>
        <span className="text-slate-500 font-medium text-xs truncate">{name}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="font-bold text-slate-800 text-xs w-24 text-right">{value}</span>
        {share !== undefined && <span className="text-[10px] font-bold bg-slate-50 text-slate-400 px-1.5 py-0.5 rounded w-9 text-center">{share}</span>}
      </div>
    </div>
  );
}

export function DashboardContent() {
  const initialToday = useRef(moscowToday());
  const autoDateRange = useRef(true);
  const [startDate, setStartDate] = useState(() => monthsBefore(initialToday.current, 6));
  const [endDate, setEndDate] = useState(() => initialToday.current);
  const [brand, setBrand] = useState('Все');
  const [activeTab, setActiveTab] = useState('Общий дашборд');
  const [brands, setBrands] = useState<string[]>(['Все']);
  const [tax, setTax] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);

  useEffect(() => {
    api<{ brands: string[] }>("/api/wb-cab/brands").then((d) => setBrands(["Все", ...(d.brands || [])])).catch(() => {});
  }, []);

  // Keep the default six-month window aligned with the Moscow calendar after midnight.
  // Once the user changes either boundary manually, their chosen range is preserved.
  useEffect(() => {
    const refreshToday = () => {
      const today = moscowToday();
      if (today === initialToday.current) return;
      initialToday.current = today;
      if (autoDateRange.current) {
        setEndDate(today);
        setStartDate(monthsBefore(today, 6));
      }
    };
    const timer = window.setInterval(refreshToday, 60_000);
    window.addEventListener('focus', refreshToday);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshToday);
    };
  }, []);

  const changeStartDate = (value: string) => { autoDateRange.current = false; setStartDate(value); };
  const changeEndDate = (value: string) => { autoDateRange.current = false; setEndDate(value); };

  useEffect(() => {
    const q = qs({ from: startDate, to: endDate, brand: brand === 'Все' ? undefined : brand });
    api(`/api/wb-cab/tax${q}&mode=usn_d`).then(setTax).catch(() => setTax(null));
    api(`/api/wb-cab/summary${q}`).then(setSummary).catch(() => setSummary(null));
  }, [startDate, endDate, brand]);

  const r = tax?.realization || {}, svc = tax?.services || {}, tc = tax?.taxes_and_costs || {};
  const o = summary?.orders || {};
  const quality = tax?.quality || {};
  const financialReady = Boolean(quality.finance_ready && quality.finance_complete);
  const profitReady = Boolean(financialReady && quality.profit_ready);
  const realization = Number(r.after_spp) || 0;
  const servicesTotal = Number(svc.total) || 0;
  const taxesTotal = (Number(tc.tax) || 0) + (Number(tc.vat) || 0) + (Number(tc.cogs) || 0);
  const profit = profitReady ? Number(tax?.operating_profit) : null;
  const orderSpark = useMemo(() => (summary?.daily || []).map((d: any) => ({ v: Number(d.orders_rub) || 0 })), [summary]);
  const financeSparks = useMemo(() => {
    const rows = tax?.daily || [];
    const series = (key: string) => rows.map((d: any) => ({ v: Number(d[key]) || 0 }));
    return { sales: series('realization'), logistics: series('logistics'), adv: series('adv'), services: series('services') };
  }, [tax]);
  const bar = (part: number, whole: number) => (whole > 0 ? Math.max(0, Math.min(100, (part / whole) * 100)) : 0);
  const salesRub = Number(r.sales_after_spp) || 0, returnsRub = Number(r.returns_after_spp) || 0;
  const financeMoney = (value: unknown) => financialReady ? money(value) : '—';
  const financePct = (part: unknown, whole: unknown) => financialReady ? pct(part, whole) : '—';
  const ordersCoverage = summary?.quality?.orders;
  const ordersSubtitle = ordersCoverage?.from && ordersCoverage?.to
    ? `${ordersCoverage.complete ? 'полный отчёт' : 'частично'} · ${ordersCoverage.from}—${ordersCoverage.to}`
    : 'данные загружаются';

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-y-auto bg-slate-100">
      <div className="bg-[#f3ece7] text-slate-700 px-4 md:px-8 py-4 text-xs md:text-sm font-medium">
        WEB-отчеты дают сводную и детальную картину по маркетплейсам прямо в личном кабинете.
      </div>

      <div className="p-4 md:p-8 flex flex-col gap-6">
        {/* Filters */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 sm:gap-6 bg-white p-4 rounded-2xl shadow-sm self-start w-full sm:w-auto">
          <div className="flex flex-wrap sm:flex-nowrap items-center gap-3">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider hidden sm:block">Период</span>
            <DatePicker value={startDate} onChange={changeStartDate} />
            <span className="text-slate-300 hidden sm:block">—</span>
            <DatePicker value={endDate} onChange={changeEndDate} />
          </div>
          <div className="hidden sm:block w-px h-6 bg-slate-200"></div>
          <div className="flex items-center gap-3 w-full sm:w-auto">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider hidden sm:block">Бренд</span>
            <div className="w-full sm:w-auto">
              <BrandSelect value={brand} onChange={setBrand} options={brands} />
            </div>
          </div>
        </div>

        {/* Main Area */}
        <div className="flex flex-col 2xl:flex-row items-start gap-6">
          {/* Tabs */}
          <div className="w-full 2xl:w-64 shrink-0 flex flex-row 2xl:flex-col gap-2 overflow-x-auto pb-2 2xl:pb-0 scrollbar-hide">
            {TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={cn(
                  "whitespace-nowrap 2xl:whitespace-normal w-auto 2xl:w-full text-left px-5 py-3.5 rounded-xl text-sm font-bold transition-all border",
                  activeTab === tab
                    ? "bg-white text-brand-600 border-brand-600 shadow-md shadow-brand-500/10"
                    : "bg-white text-slate-700 border-transparent hover:border-slate-200 shadow-sm hover:shadow"
                )}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Right Content */}
          <div className="flex-1 min-w-0 w-full flex flex-col gap-6">
            {activeTab === 'РНП' ? (
              <RnpTab brand={brand} startDate={startDate} endDate={endDate} />
            ) : activeTab === 'Настройка' ? (
              <SettingsContent />
            ) : activeTab === 'Общий дашборд' ? (
              <>
                {!financialReady && (
                  <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                    <div>
                      <div className="font-bold">Финансовый отчёт WB ещё загружается</div>
                      <div className="mt-0.5 text-xs leading-5 text-amber-800">
                        Реализация, услуги, налоги и прибыль скрыты до завершения всего отчёта за выбранный период — частичные строки не выдаются за итоговые. Оперативные заказы ниже показаны отдельно с фактическим покрытием.
                      </div>
                    </div>
                  </div>
                )}
                {financialReady && !profitReady && (
                  <div className="flex items-start gap-3 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950">
                    <Info className="mt-0.5 h-5 w-5 shrink-0 text-sky-500" />
                    <div>
                      <div className="font-bold">Финансовый отчёт загружен, прибыль пока не рассчитана</div>
                      <div className="mt-0.5 text-xs leading-5 text-sky-800">
                        Не заполнена себестоимость для {Number(quality.missing_cost_barcodes) || 0} баркодов. До заполнения этих данных показывать прибыль как окончательную было бы неверно.
                      </div>
                    </div>
                  </div>
                )}
                {/* Top Row Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 xl:grid-cols-4 gap-4">
                  {/* Card 1: Реализация */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Реализация</div>
                    <div className="text-3xl font-black text-slate-900 mb-6 tracking-tight">{financeMoney(realization)}</div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#10b981] h-full" style={{ width: `${bar(salesRub, salesRub + returnsRub)}%` }}></div>
                      <div className="bg-[#ef4444] h-full" style={{ width: `${bar(returnsRub, salesRub + returnsRub)}%` }}></div>
                    </div>
                    <div className="space-y-3 text-[11px]">
                      <Row color="#10b981" name="Продажи по финотчёту" value={financeMoney(salesRub)} ring />
                      <Row color="#ef4444" name="Возвраты по финотчёту" value={financeMoney(returnsRub)} ring />
                    </div>
                  </div>

                  {/* Card 2: Услуги */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Услуги</div>
                    <div className="flex items-baseline gap-3 mb-6">
                      <div className="text-3xl font-black text-slate-900 tracking-tight">{financeMoney(servicesTotal)}</div>
                      <div className="text-[11px] font-bold bg-slate-100 text-slate-500 px-2 py-1 rounded-md">{financePct(servicesTotal, realization)}</div>
                    </div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#f59e0b] h-full" style={{ width: `${bar(Number(svc.commission) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#60a5fa] h-full" style={{ width: `${bar(Number(svc.logistics) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#a855f7] h-full" style={{ width: `${bar(Number(svc.adv) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#3b82f6] h-full" style={{ width: `${bar((Number(svc.storage) || 0) + (Number(svc.other) || 0), servicesTotal)}%` }}></div>
                    </div>
                    <div className="space-y-2.5 text-[11px]">
                      <Row color="#f59e0b" name="Комиссия WB" value={financeMoney(svc.commission)} share={financePct(svc.commission, realization)} />
                      <Row color="#60a5fa" name="Логистика" value={financeMoney(svc.logistics)} share={financePct(svc.logistics, realization)} />
                      <Row color="#a855f7" name="Реклама" value={quality.advertising_allocated === false ? '—' : financeMoney(svc.adv)} share={quality.advertising_allocated === false ? '—' : financePct(svc.adv, realization)} />
                      <Row color="#3b82f6" name="Остальные" value={financeMoney((Number(svc.storage) || 0) + (Number(svc.other) || 0))} share={financePct((Number(svc.storage) || 0) + (Number(svc.other) || 0), realization)} />
                    </div>
                  </div>

                  {/* Card 3: Налоги и затраты */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Налоги и затраты</div>
                    <div className="flex items-baseline gap-3 mb-6">
                      <div className="text-3xl font-black text-slate-900 tracking-tight">{financeMoney(taxesTotal)}</div>
                      <div className="text-[11px] font-bold bg-slate-100 text-slate-500 px-2 py-1 rounded-md">{financePct(taxesTotal, realization)}</div>
                    </div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#f97316] h-full" style={{ width: `${bar(Number(tc.tax) || 0, taxesTotal)}%` }}></div>
                      <div className="bg-[#ec4899] h-full" style={{ width: `${bar(Number(tc.vat) || 0, taxesTotal)}%` }}></div>
                      <div className="bg-[#a1a1aa] h-full" style={{ width: `${bar(Number(tc.cogs) || 0, taxesTotal)}%` }}></div>
                    </div>
                    <div className="space-y-2 text-[11px]">
                      <Row color="#f97316" name="Налог" value={financeMoney(tc.tax)} share={financePct(tc.tax, realization)} />
                      <Row color="#ec4899" name="НДС к уплате" value={financeMoney(tc.vat)} share={financePct(tc.vat, realization)} />
                      <Row color="#a1a1aa" name="Себестоимость продаж" value={quality.costs_ready ? financeMoney(tc.cogs) : '—'} share={quality.costs_ready ? financePct(tc.cogs, realization) : '—'} />
                      <Row color="#78350f" name="Себестоимость самовыкупов" value="—" share="—" />
                      <Row color="#84cc16" name="Прочие затраты" value="—" share="—" />
                    </div>
                  </div>

                  {/* Card 4: Операционная прибыль */}
                  <div className="bg-gradient-to-br from-white to-slate-50 p-6 rounded-2xl shadow-sm border border-slate-200 flex flex-col relative transition-shadow hover:shadow-md">
                    <div className="flex justify-between items-center mb-2">
                      <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Операционная прибыль</div>
                      <Info className="w-4 h-4 text-slate-300 hover:text-brand-500 transition-colors cursor-help" />
                    </div>
                    <div className={cn("text-4xl font-black mb-6 tracking-tight drop-shadow-sm", profit !== null && profit < 0 ? "text-[#ef4444]" : "text-[#10b981]")}>
                      {profitReady ? money(profit) : '—'} {profitReady && <span className="text-2xl text-slate-400 font-bold">₽</span>}
                    </div>
                    <div className="space-y-3 mb-6">
                      <div className="flex justify-between items-center">
                        <span className="text-slate-500 font-medium text-sm">Маржинальность</span>
                        <span className="font-black text-slate-800 bg-white shadow-sm border border-slate-100 px-2 py-0.5 rounded-lg">{profitReady ? pct(profit, realization) : '—'}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-slate-500 font-medium text-sm">Рентабельность</span>
                        <span className="font-black text-slate-800 bg-white shadow-sm border border-slate-100 px-2 py-0.5 rounded-lg">{profitReady ? pct(profit, servicesTotal + (Number(tc.cogs) || 0)) : '—'}</span>
                      </div>
                    </div>
                    <div className="h-24 mt-auto -mx-2 -mb-2"><Sparkline data={profitReady ? financeSparks.sales : []} color="#10b981" /></div>
                  </div>
                </div>

                {/* Bottom Row Cards */}
                <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
                  <BottomCard title="Заказы" value={money(o.orders_rub)} color="#f97316" spark={orderSpark} subtitle={ordersSubtitle} />
                  <BottomCard title="Продажи" value={financeMoney(realization)} color="#10b981" spark={financialReady ? financeSparks.sales : []} subtitle="по финансовому отчёту WB" />
                  <BottomCard title="Логистика" value={financeMoney(svc.logistics)} color="#3b82f6" spark={financialReady ? financeSparks.logistics : []} subtitle="по финансовому отчёту WB" />
                  <BottomCard title="Реклама" value={quality.advertising_allocated === false ? '—' : financeMoney(svc.adv)} color="#a855f7" spark={financialReady ? financeSparks.adv : []} subtitle="по списаниям рекламного кабинета" />
                  <BottomCard title="Все услуги" value={financeMoney(servicesTotal)} color="#3b82f6" spark={financialReady ? financeSparks.services : []} subtitle="по финансовому отчёту WB" />
                </div>

                {(!summary || ((Number(o.orders_cnt) || 0) === 0 && !financialReady)) && (
                  <div className="bg-white border border-dashed border-slate-200 rounded-2xl p-5 text-center text-sm font-medium text-slate-500">
                    За выбранный период пока нет данных заказов/продаж — цифры появятся автоматически по мере синхронизации с WB.
                  </div>
                )}
              </>
            ) : (
              <div className="bg-white p-12 rounded-2xl shadow-sm border border-slate-100 flex flex-col items-center justify-center text-center">
                <LayoutDashboard className="w-16 h-16 text-slate-200 mb-4" />
                <h2 className="text-xl font-bold text-slate-900 mb-2">Раздел «{activeTab}» скоро заполнится</h2>
                <p className="text-slate-500 max-w-md">Данные подключаются автоматически из нашей базы. Раздел появится после загрузки соответствующей статистики WB{['ОПиУ', 'ДДС'].includes(activeTab) ? ' и настройки себестоимости' : ''}.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function BottomCard({ title, value, color, spark, subtitle = 'за период' }: { title: string, value: string, color: string, spark: { v: number }[], subtitle?: string }) {
  return (
    <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 flex flex-col h-40 transition-shadow hover:shadow-md">
      <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">{title}</div>
      <div className="text-[10px] font-medium text-slate-400 mb-2 truncate" title={subtitle}>{subtitle}</div>
      <div className="text-xl font-black text-slate-900 mb-1 tracking-tight">{value}</div>
      <div className="h-12 mt-auto -mx-2 -mb-2"><Sparkline data={spark} color={color} /></div>
    </div>
  );
}
