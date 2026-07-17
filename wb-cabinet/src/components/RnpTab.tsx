import { useEffect, useMemo, useRef, useState } from 'react';
import { Image as ImageIcon, Search } from 'lucide-react';
import { cn } from '../lib/utils';
import { api, qs } from '../lib/api';

type Card = {
  nm_id: number;
  vendor_code: string | null;
  title: string | null;
  brand: string | null;
  subject_name: string | null;
  photo_url: string | null;
};

type RnpDay = {
  day: string;
  orders_cnt: number;
  orders_rub: number;
  sales_cnt: number;
  sales_rub: number;
  returns_cnt: number;
  for_pay_rub: number;
  cogs_rub: number;
  stock_qty: number;
  adv_rub: number;
  drr_pct: number;
};

type MetricPoint = RnpDay & {
  cumulativeOrders: number;
  buyoutPct: number;
  turnoverDays: number;
  depletionDate: string;
  operatingProfit: number;
  operatingProfitPerUnit: number;
  profitPerUnit: number;
  profitabilityPct: number;
  roiPct: number;
};

type MetricRow = {
  label: string;
  value: (point: MetricPoint) => number | string;
  summary: (points: MetricPoint[]) => number | string;
  format?: 'count' | 'money' | 'percent' | 'days' | 'date';
  tone?: 'blue' | 'red' | 'green' | 'muted';
};

const ZERO_DAY = (day: string): RnpDay => ({
  day,
  orders_cnt: 0,
  orders_rub: 0,
  sales_cnt: 0,
  sales_rub: 0,
  returns_cnt: 0,
  for_pay_rub: 0,
  cogs_rub: 0,
  stock_qty: 0,
  adv_rub: 0,
  drr_pct: 0,
});

const number = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

function dateRange(from: string, to: string): string[] {
  const start = new Date(`${from}T00:00:00`);
  const end = new Date(`${to}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return [];
  const result: string[] = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    result.push(`${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`);
    cursor.setDate(cursor.getDate() + 1);
  }
  return result;
}

function shortDate(value: string) {
  const [, month = '', day = ''] = value.split('-');
  return `${day}.${month}`;
}

function fullDate(value: string) {
  const [year = '', month = '', day = ''] = value.split('-');
  return `${day}.${month}.${year}`;
}

function addDays(value: string, days: number) {
  if (days <= 0) return '—';
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + Math.ceil(days));
  return date.toLocaleDateString('ru-RU');
}

const sum = (points: MetricPoint[], key: keyof RnpDay) => points.reduce((total, point) => total + number(point[key]), 0);
const average = (values: number[]) => values.length ? values.reduce((total, value) => total + value, 0) / values.length : 0;
const ratio = (part: number, whole: number) => whole > 0 ? part / whole * 100 : 0;
const divide = (part: number, whole: number) => whole > 0 ? part / whole : 0;

function formatValue(value: number | string, format: MetricRow['format'] = 'count') {
  if (typeof value === 'string') return value;
  if (format === 'money') return `${value.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
  if (format === 'percent') return `${value.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  if (format === 'days') return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} дн.`;
  if (format === 'date') return value ? String(value) : '—';
  return `${Math.round(value).toLocaleString('ru-RU')} шт`;
}

const PLAN_ROWS: MetricRow[] = [
  { label: 'План заказов, шт', value: () => 0, summary: () => 0 },
  { label: 'Факт заказов, шт', value: (p) => p.orders_cnt, summary: (p) => sum(p, 'orders_cnt') },
  { label: '% выполнения плана', value: () => 0, summary: () => 0, format: 'percent', tone: 'blue' },
  { label: 'План заказов, шт. накопительный', value: () => 0, summary: () => 0 },
  { label: 'Факт заказов, шт. накопительный', value: (p) => p.cumulativeOrders, summary: (p) => sum(p, 'orders_cnt') },
  { label: '% выполнения плана накопительный', value: () => 0, summary: () => 0, format: 'percent', tone: 'blue' },
  { label: 'План заказов, руб', value: () => 0, summary: () => 0, format: 'money' },
  { label: 'Факт заказов, руб', value: (p) => p.orders_rub, summary: (p) => sum(p, 'orders_rub'), format: 'money' },
  { label: '% выполнения плана', value: () => 0, summary: () => 0, format: 'percent', tone: 'blue' },
];

const MAIN_ROWS: MetricRow[] = [
  { label: 'Заказы, шт', value: (p) => p.orders_cnt, summary: (p) => sum(p, 'orders_cnt') },
  { label: 'Заказы, руб', value: (p) => p.orders_rub, summary: (p) => sum(p, 'orders_rub'), format: 'money' },
  { label: 'Раздачи, шт (по дате заказа)', value: () => 0, summary: () => 0 },
  { label: '% Выкупа', value: (p) => p.buyoutPct, summary: (p) => ratio(sum(p, 'sales_cnt'), sum(p, 'orders_cnt')), format: 'percent', tone: 'blue' },
  { label: 'Продажи, шт. (прогнозные)', value: (p) => p.sales_cnt, summary: (p) => sum(p, 'sales_cnt') },
  { label: 'Продажи, руб (прогнозные)', value: (p) => p.sales_rub, summary: (p) => sum(p, 'sales_rub'), format: 'money' },
  { label: 'Затраты РК, руб', value: (p) => p.adv_rub, summary: (p) => sum(p, 'adv_rub'), format: 'money', tone: 'red' },
  { label: 'ДРР РК, %', value: (p) => p.drr_pct, summary: (p) => ratio(sum(p, 'adv_rub'), sum(p, 'orders_rub')), format: 'percent' },
  { label: 'ДРР ко всем заказам, %', value: (p) => ratio(p.adv_rub, p.orders_rub), summary: (p) => ratio(sum(p, 'adv_rub'), sum(p, 'orders_rub')), format: 'percent' },
  { label: 'ДРР к продажам, %', value: (p) => ratio(p.adv_rub, p.sales_rub), summary: (p) => ratio(sum(p, 'adv_rub'), sum(p, 'sales_rub')), format: 'percent' },
  { label: 'Остаток WB, шт', value: (p) => p.stock_qty, summary: (p) => p.at(-1)?.stock_qty || 0, tone: 'green' },
  { label: 'Оборачиваемость склада, дн.', value: (p) => p.turnoverDays, summary: (p) => average(p.map((item) => item.turnoverDays)), format: 'days', tone: 'green' },
  { label: 'Остаток закончится WB', value: (p) => p.depletionDate, summary: (p) => p.at(-1)?.depletionDate || '—', format: 'date', tone: 'muted' },
  { label: 'К перечислению на р/сч, руб (прогнозное)', value: (p) => p.for_pay_rub, summary: (p) => sum(p, 'for_pay_rub'), format: 'money' },
  { label: 'Опер. прибыль на единицу, руб (прогнозная)', value: (p) => p.operatingProfitPerUnit, summary: (p) => divide(sum(p, 'for_pay_rub') - sum(p, 'cogs_rub') - sum(p, 'adv_rub'), sum(p, 'sales_cnt')), format: 'money', tone: 'green' },
  { label: 'Прибыль на единицу, руб (прогнозная)', value: (p) => p.profitPerUnit, summary: (p) => divide(sum(p, 'for_pay_rub') - sum(p, 'cogs_rub') - sum(p, 'adv_rub'), sum(p, 'sales_cnt')), format: 'money' },
  { label: 'Рентабельность, %', value: (p) => p.profitabilityPct, summary: (p) => ratio(sum(p, 'for_pay_rub') - sum(p, 'cogs_rub') - sum(p, 'adv_rub'), sum(p, 'sales_rub')), format: 'percent', tone: 'blue' },
  { label: 'ROI, %', value: (p) => p.roiPct, summary: (p) => ratio(sum(p, 'for_pay_rub') - sum(p, 'cogs_rub') - sum(p, 'adv_rub'), sum(p, 'cogs_rub')), format: 'percent' },
];

function TableSkeleton({ title, accent, rowCount }: { title: string; accent: 'navy' | 'purple'; rowCount: number }) {
  const heading = accent === 'navy' ? 'bg-slate-800' : 'bg-violet-500';
  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" aria-busy="true">
      <div className={`${heading} px-4 py-3 text-xs font-black uppercase tracking-wide text-white sm:px-5`}>{title}</div>
      <div className="flex flex-col gap-3 p-4 sm:p-5">
        {Array.from({ length: rowCount }, (_, index) => (
          <div key={index} className="flex items-center gap-3">
            <div className="skeleton h-4 w-20 shrink-0 rounded-md" />
            <div className="skeleton h-4 w-44 shrink-0 rounded-md" />
            <div className="skeleton h-4 flex-1 rounded-md" style={{ opacity: 1 - index * 0.06 }} />
          </div>
        ))}
      </div>
    </section>
  );
}

function MetricsTable({ title, rows, points, accent }: { title: string; rows: MetricRow[]; points: MetricPoint[]; accent: 'navy' | 'purple' }) {
  const visible = [...points].reverse();
  const heading = accent === 'navy' ? 'bg-slate-800' : 'bg-violet-500';
  const tone = (row: MetricRow) => row.tone === 'blue' ? 'bg-sky-50 text-sky-700' : row.tone === 'red' ? 'bg-red-50 text-red-600' : row.tone === 'green' ? 'bg-emerald-50 text-emerald-700' : row.tone === 'muted' ? 'bg-slate-50 text-slate-700' : 'bg-white text-slate-700';

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className={`${heading} px-4 py-3 text-xs font-black uppercase tracking-wide text-white sm:px-5`}>{title}</div>
      <div className="max-w-full overflow-x-auto overscroll-x-contain">
        <table className="min-w-max border-separate border-spacing-0 text-[11px] sm:text-xs">
          <thead>
            <tr className="bg-indigo-50 text-[9px] font-black uppercase tracking-wide text-slate-700 sm:text-[10px]">
              <th className="sticky left-0 z-30 w-28 min-w-28 border-b border-r border-indigo-100 bg-indigo-50 px-2 py-3 text-center sm:w-36 sm:min-w-36">Итого / ср.</th>
              <th className="sticky left-28 z-30 w-48 min-w-48 border-b border-r border-indigo-100 bg-indigo-50 px-3 py-3 text-left sm:left-36 sm:w-64 sm:min-w-64">Показатель</th>
              {visible.map((point) => <th key={point.day} className="w-28 min-w-28 border-b border-indigo-100 px-3 py-3 text-center">{shortDate(point.day)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label} className="group">
                <td className="sticky left-0 z-20 border-b border-r border-slate-100 bg-white px-2 py-3 text-center font-black text-slate-900 group-last:border-b-0">
                  {formatValue(row.summary(points), row.format)}
                </td>
                <th className="sticky left-28 z-20 border-b border-r border-slate-100 bg-white px-3 py-3 text-left font-medium text-slate-600 group-last:border-b-0 sm:left-36">
                  {row.label}
                </th>
                {visible.map((point) => (
                  <td key={point.day} className={`border-b border-slate-100 px-3 py-3 text-center font-medium group-last:border-b-0 ${tone(row)}`}>
                    {formatValue(row.value(point), row.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RnpTab({ brand, startDate, endDate }: { brand?: string; startDate: string; endDate: string }) {
  const [search, setSearch] = useState('');
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(false);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [metricsLoadedOnce, setMetricsLoadedOnce] = useState(false);
  const [metricsError, setMetricsError] = useState(false);
  const [selected, setSelected] = useState<Card | null>(null);
  const [days, setDays] = useState<RnpDay[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true);
      api<{ cards: Card[] }>(`/api/wb-cab/cards${qs({ q: search, brand: brand === 'Все' ? undefined : brand })}`)
        .then((data) => setCards(data.cards || []))
        .catch(() => setCards([]))
        .finally(() => setLoading(false));
    }, 220);
    return () => clearTimeout(timer);
  }, [search, brand]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) setIsSearching(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    setMetricsLoading(true);
    setMetricsError(false);
    api<{ days: RnpDay[] }>(`/api/wb-cab/rnp${qs({
      from: startDate,
      to: endDate,
      brand: brand === 'Все' ? undefined : brand,
      nm_id: selected ? String(selected.nm_id) : undefined,
    })}`)
      .then((data) => setDays(data.days || []))
      .catch(() => { setDays([]); setMetricsError(true); })
      .finally(() => { setMetricsLoading(false); setMetricsLoadedOnce(true); });
  }, [selected, startDate, endDate, brand]);

  const points = useMemo<MetricPoint[]>(() => {
    const byDay = new Map(days.map((day) => [day.day, day]));
    let cumulativeOrders = 0;
    return dateRange(startDate, endDate).map((date) => {
      const source = byDay.get(date) || ZERO_DAY(date);
      const normalized: RnpDay = {
        day: date,
        orders_cnt: number(source.orders_cnt),
        orders_rub: number(source.orders_rub),
        sales_cnt: number(source.sales_cnt),
        sales_rub: number(source.sales_rub),
        returns_cnt: number(source.returns_cnt),
        for_pay_rub: number(source.for_pay_rub),
        cogs_rub: number(source.cogs_rub),
        stock_qty: number(source.stock_qty),
        adv_rub: number(source.adv_rub),
        drr_pct: number(source.drr_pct),
      };
      cumulativeOrders += normalized.orders_cnt;
      const operatingProfit = normalized.for_pay_rub - normalized.cogs_rub - normalized.adv_rub;
      const turnoverDays = normalized.sales_cnt > 0 ? normalized.stock_qty / normalized.sales_cnt : 0;
      return {
        ...normalized,
        cumulativeOrders,
        buyoutPct: ratio(normalized.sales_cnt, normalized.orders_cnt),
        turnoverDays,
        depletionDate: addDays(date, turnoverDays),
        operatingProfit,
        operatingProfitPerUnit: normalized.sales_cnt > 0 ? operatingProfit / normalized.sales_cnt : 0,
        profitPerUnit: normalized.sales_cnt > 0 ? operatingProfit / normalized.sales_cnt : 0,
        profitabilityPct: ratio(operatingProfit, normalized.sales_rub),
        roiPct: ratio(operatingProfit, normalized.cogs_rub),
      };
    });
  }, [days, startDate, endDate]);

  return (
    <div className="flex w-full max-w-full min-w-0 flex-col gap-5 sm:gap-6">
      <div className="flex flex-col gap-5 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm sm:p-6 lg:p-8">
        <div>
          <h2 className="mb-1 text-lg font-bold text-slate-900 sm:text-xl">Аналитика по артикулу (РНП)</h2>
          <p className="text-sm font-medium text-slate-500">Выберите товар для детального анализа. Базовый шаблон показателей доступен всегда.</p>
        </div>

        <div className="relative w-full max-w-xl" ref={searchRef}>
          <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            className="block w-full rounded-xl border border-slate-200 bg-slate-50 py-3.5 pl-11 pr-4 text-sm font-medium text-slate-900 shadow-sm transition-all placeholder:text-slate-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-500"
            placeholder="Артикул WB, артикул поставщика или название…"
            value={search}
            onChange={(event) => { setSearch(event.target.value); setIsSearching(true); }}
            onFocus={() => setIsSearching(true)}
          />
          {isSearching && (
            <div className="absolute z-50 mt-2 w-full overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl">
              {loading ? (
                <div className="flex flex-col gap-3 px-4 py-4" aria-busy="true" aria-label="Загружаю каталог">
                  {[0, 1, 2].map((index) => (
                    <div key={index} className="flex items-center gap-3">
                      <div className="skeleton h-11 w-11 shrink-0 rounded-lg" />
                      <div className="flex min-w-0 flex-1 flex-col gap-2">
                        <div className="skeleton h-3.5 w-28 rounded-md" />
                        <div className="skeleton h-3 w-3/4 rounded-md" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : cards.length > 0 ? (
                <ul className="max-h-80 divide-y divide-slate-100 overflow-auto">
                  {cards.map((card) => (
                    <li key={card.nm_id}>
                      <button
                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 sm:gap-4"
                        onClick={() => { setSelected(card); setSearch(String(card.nm_id)); setIsSearching(false); }}
                      >
                        <div className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm sm:h-12 sm:w-12">
                          {card.photo_url ? <img src={card.photo_url} alt={card.title || ''} className="h-full w-full object-cover" /> : <ImageIcon className="h-5 w-5 text-slate-400" />}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="shrink-0 rounded bg-brand-50 px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wide text-brand-600">WB</span>
                            <span className="truncate font-black text-slate-900">{card.nm_id}</span>
                          </div>
                          <div className="mt-0.5 line-clamp-1 text-xs font-medium text-slate-600">{card.title || card.subject_name || ''}</div>
                          {card.vendor_code && <div className="mt-0.5 truncate text-[10px] font-medium text-slate-400">Арт. поставщика: {card.vendor_code}</div>}
                        </div>
                        {card.brand && <span className="hidden shrink-0 text-[10px] font-bold uppercase text-slate-400 sm:block">{card.brand}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="px-4 py-8 text-center text-sm font-medium text-slate-500">Ничего не найдено</div>
              )}
            </div>
          )}
        </div>

        {selected ? (
          <div className="flex items-center gap-4 border-t border-slate-100 pt-5 sm:gap-6 sm:pt-6">
            <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 shadow-sm sm:h-24 sm:w-24">
              {selected.photo_url ? <img src={selected.photo_url} alt={selected.title || ''} className="h-full w-full object-cover" /> : <ImageIcon className="h-7 w-7 text-slate-300" />}
            </div>
            <div className="min-w-0">
              <div className="mb-2 inline-flex rounded-md bg-brand-50 px-2.5 py-1 text-[9px] font-bold uppercase tracking-wider text-brand-600 sm:text-[10px]">Выбранный товар</div>
              <div className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 text-xs font-bold text-slate-400">Артикул WB</span>
                <span className="truncate text-xl font-black tracking-tight text-slate-900 sm:text-2xl">{selected.nm_id}</span>
              </div>
              <div className="line-clamp-2 text-xs font-medium text-slate-500 sm:text-sm">{selected.title || selected.subject_name || ''}</div>
              <div className="mt-1 text-[11px] font-medium text-slate-400">
                {selected.vendor_code ? `Арт. поставщика: ${selected.vendor_code}` : 'Артикул поставщика не указан'}{selected.brand ? ` · ${selected.brand}` : ''}
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-xs font-medium text-indigo-700 sm:text-sm">
            Показана сводка по всему WB-кабинету за полгода: {fullDate(startDate)}–{fullDate(endDate)}. Выберите товар, чтобы отфильтровать факты по его артикулу WB.
          </div>
        )}
      </div>

      {metricsError && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-medium text-amber-800 sm:text-sm">
          Данные временно недоступны. Шаблон сохранён и показан с нулевыми значениями.
        </div>
      )}

      {!metricsLoadedOnce ? (
        <>
          <TableSkeleton title="План / факт" accent="navy" rowCount={6} />
          <TableSkeleton title="Основные метрики" accent="purple" rowCount={10} />
        </>
      ) : (
        <div className={cn('flex min-w-0 flex-col gap-5 sm:gap-6 fade-in', metricsLoading && 'refetching')}>
          <MetricsTable title="План / факт" rows={PLAN_ROWS} points={points} accent="navy" />
          <MetricsTable title="Основные метрики" rows={MAIN_ROWS} points={points} accent="purple" />
        </div>
      )}
    </div>
  );
}
