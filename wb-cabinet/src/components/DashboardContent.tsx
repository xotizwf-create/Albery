import { useEffect, useMemo, useState } from "react";
import { AreaChart, Area, ResponsiveContainer } from 'recharts';
import { Info, LayoutDashboard } from "lucide-react";
import { cn } from "../lib/utils";
import { DatePicker, BrandSelect } from "./shared/FormControls";
import { RnpTab } from "./RnpTab";
import { SettingsContent } from "./SettingsContent";
import { api, qs, money, pct } from "../lib/api";

const TABS = ["Общий дашборд", "РНП", "ОПиУ", "ДДС", "По артикулам", "Налоговый калькулятор", "Настройка"];

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
  const [startDate, setStartDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 29); return d.toISOString().slice(0, 10); });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [brand, setBrand] = useState('Все');
  const [activeTab, setActiveTab] = useState('Общий дашборд');
  const [brands, setBrands] = useState<string[]>(['Все']);
  const [tax, setTax] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);

  useEffect(() => {
    api<{ brands: string[] }>("/api/wb-cab/brands").then((d) => setBrands(["Все", ...(d.brands || [])])).catch(() => {});
  }, []);

  useEffect(() => {
    const q = qs({ from: startDate, to: endDate, brand: brand === 'Все' ? undefined : brand });
    api(`/api/wb-cab/tax${q}&mode=usn_d`).then(setTax).catch(() => setTax(null));
    api(`/api/wb-cab/summary${q}`).then(setSummary).catch(() => setSummary(null));
  }, [startDate, endDate, brand]);

  const r = tax?.realization || {}, svc = tax?.services || {}, tc = tax?.taxes_and_costs || {};
  const o = summary?.orders || {}, s = summary?.sales || {};
  const realization = Number(r.before_spp) || 0;
  const servicesTotal = Number(svc.total) || 0;
  const taxesTotal = (Number(tc.tax) || 0) + (Number(tc.vat) || 0) + (Number(tc.cogs) || 0);
  const profit = Number(tax?.operating_profit) || 0;
  const spark = useMemo(() => (summary?.daily || []).map((d: any) => ({ v: Number(d.orders_rub) || 0 })), [summary]);
  const bar = (part: number, whole: number) => (whole > 0 ? Math.max(0, Math.min(100, (part / whole) * 100)) : 0);
  const salesRub = Number(s.sales_rub) || 0, returnsRub = Number(s.returns_rub) || 0;

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
            <DatePicker value={startDate} onChange={setStartDate} />
            <span className="text-slate-300 hidden sm:block">—</span>
            <DatePicker value={endDate} onChange={setEndDate} />
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
                {/* Top Row Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 xl:grid-cols-4 gap-4">
                  {/* Card 1: Реализация */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Реализация</div>
                    <div className="text-3xl font-black text-slate-900 mb-6 tracking-tight">{money(realization)}</div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#10b981] h-full" style={{ width: `${bar(salesRub, salesRub + returnsRub)}%` }}></div>
                      <div className="bg-[#ef4444] h-full" style={{ width: `${bar(returnsRub, salesRub + returnsRub)}%` }}></div>
                    </div>
                    <div className="space-y-3 text-[11px]">
                      <Row color="#10b981" name="Продажи" value={money(s.sales_rub)} ring />
                      <Row color="#ef4444" name="Возвраты" value={money(s.returns_rub)} ring />
                    </div>
                  </div>

                  {/* Card 2: Услуги */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Услуги</div>
                    <div className="flex items-baseline gap-3 mb-6">
                      <div className="text-3xl font-black text-slate-900 tracking-tight">{money(servicesTotal)}</div>
                      <div className="text-[11px] font-bold bg-slate-100 text-slate-500 px-2 py-1 rounded-md">{pct(servicesTotal, realization)}</div>
                    </div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#f59e0b] h-full" style={{ width: `${bar(Number(svc.commission) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#60a5fa] h-full" style={{ width: `${bar(Number(svc.logistics) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#a855f7] h-full" style={{ width: `${bar(Number(svc.adv) || 0, servicesTotal)}%` }}></div>
                      <div className="bg-[#3b82f6] h-full" style={{ width: `${bar((Number(svc.storage) || 0) + (Number(svc.other) || 0), servicesTotal)}%` }}></div>
                    </div>
                    <div className="space-y-2.5 text-[11px]">
                      <Row color="#f59e0b" name="Комиссия" value={money(svc.commission)} share={pct(svc.commission, realization)} />
                      <Row color="#60a5fa" name="Логистика" value={money(svc.logistics)} share={pct(svc.logistics, realization)} />
                      <Row color="#a855f7" name="Реклама" value={money(svc.adv)} share={pct(svc.adv, realization)} />
                      <Row color="#3b82f6" name="Остальные" value={money((Number(svc.storage) || 0) + (Number(svc.other) || 0))} share={pct((Number(svc.storage) || 0) + (Number(svc.other) || 0), realization)} />
                    </div>
                  </div>

                  {/* Card 3: Налоги и затраты */}
                  <div className="bg-white p-6 rounded-2xl shadow-sm flex flex-col border border-slate-100 transition-shadow hover:shadow-md">
                    <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Налоги и затраты</div>
                    <div className="flex items-baseline gap-3 mb-6">
                      <div className="text-3xl font-black text-slate-900 tracking-tight">{money(taxesTotal)}</div>
                      <div className="text-[11px] font-bold bg-slate-100 text-slate-500 px-2 py-1 rounded-md">{pct(taxesTotal, realization)}</div>
                    </div>
                    <div className="h-2 w-full bg-slate-100 rounded-full flex mb-6 overflow-hidden">
                      <div className="bg-[#f97316] h-full" style={{ width: `${bar(Number(tc.tax) || 0, taxesTotal)}%` }}></div>
                      <div className="bg-[#ec4899] h-full" style={{ width: `${bar(Number(tc.vat) || 0, taxesTotal)}%` }}></div>
                      <div className="bg-[#a1a1aa] h-full" style={{ width: `${bar(Number(tc.cogs) || 0, taxesTotal)}%` }}></div>
                    </div>
                    <div className="space-y-2 text-[11px]">
                      <Row color="#f97316" name="Налог" value={money(tc.tax)} share={pct(tc.tax, realization)} />
                      <Row color="#ec4899" name="НДС к уплате" value={money(tc.vat)} share={pct(tc.vat, realization)} />
                      <Row color="#a1a1aa" name="Себестоимость продаж" value={money(tc.cogs)} share={pct(tc.cogs, realization)} />
                      <Row color="#78350f" name="Себестоимость самовыкупов" value={money(0)} share="0%" />
                      <Row color="#84cc16" name="Затраты" value={money(0)} share="0%" />
                    </div>
                  </div>

                  {/* Card 4: Операционная прибыль */}
                  <div className="bg-gradient-to-br from-white to-slate-50 p-6 rounded-2xl shadow-sm border border-slate-200 flex flex-col relative transition-shadow hover:shadow-md">
                    <div className="flex justify-between items-center mb-2">
                      <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Операционная прибыль</div>
                      <Info className="w-4 h-4 text-slate-300 hover:text-brand-500 transition-colors cursor-help" />
                    </div>
                    <div className={cn("text-4xl font-black mb-6 tracking-tight drop-shadow-sm", profit < 0 ? "text-[#ef4444]" : "text-[#10b981]")}>
                      {money(profit)} <span className="text-2xl text-slate-400 font-bold">₽</span>
                    </div>
                    <div className="space-y-3 mb-6">
                      <div className="flex justify-between items-center">
                        <span className="text-slate-500 font-medium text-sm">Маржинальность</span>
                        <span className="font-black text-slate-800 bg-white shadow-sm border border-slate-100 px-2 py-0.5 rounded-lg">{pct(profit, realization)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-slate-500 font-medium text-sm">Рентабельность</span>
                        <span className="font-black text-slate-800 bg-white shadow-sm border border-slate-100 px-2 py-0.5 rounded-lg">{pct(profit, servicesTotal + (Number(tc.cogs) || 0))}</span>
                      </div>
                    </div>
                    <div className="h-24 mt-auto -mx-2 -mb-2"><Sparkline data={spark} color="#10b981" /></div>
                  </div>
                </div>

                {/* Bottom Row Cards */}
                <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
                  <BottomCard title="Заказы" value={money(o.orders_rub)} color="#f97316" spark={spark} />
                  <BottomCard title="Продажи" value={money(s.sales_rub)} color="#10b981" spark={spark} />
                  <BottomCard title="Логистика" value={money(svc.logistics)} color="#3b82f6" spark={spark} />
                  <BottomCard title="Реклама" value={money(svc.adv)} color="#a855f7" spark={spark} />
                  <BottomCard title="Все услуги" value={money(servicesTotal)} color="#3b82f6" spark={spark} />
                </div>

                {(!summary || ((Number(o.orders_cnt) || 0) === 0 && (Number(s.sales_cnt) || 0) === 0)) && (
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

function BottomCard({ title, value, color, spark }: { title: string, value: string, color: string, spark: { v: number }[] }) {
  return (
    <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 flex flex-col h-40 transition-shadow hover:shadow-md">
      <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">{title}</div>
      <div className="text-[10px] font-medium text-slate-400 mb-2">за период</div>
      <div className="text-xl font-black text-slate-900 mb-1 tracking-tight">{value}</div>
      <div className="h-12 mt-auto -mx-2 -mb-2"><Sparkline data={spark} color={color} /></div>
    </div>
  );
}
