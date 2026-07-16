import { useState, useRef, useEffect, Fragment, ClipboardEvent } from 'react';
import { Package, Target, Save, EyeOff, Eye, Calendar as CalendarIcon, ChevronLeft, ChevronRight, ChevronDown, Loader2 } from 'lucide-react';
import { cn } from '../lib/utils';
import { api, apiPost } from '../lib/api';

const SETTINGS_TABS = [
  { id: 'articles', label: 'Артикулы и себестоимость', icon: Package, description: 'Управление каталогом и еженедельной себестоимостью' },
  { id: 'sales_plan', label: 'План продаж', icon: Target, description: 'Установка KPI и плановых показателей на период' },
];

type Card = { nm_id: number; vendor_code: string | null; title: string | null; brand: string | null; subject_name: string | null; photo_url: string | null; excluded: boolean };

export function SettingsContent() {
  const [activeTab, setActiveTab] = useState('articles');
  const [articles, setArticles] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);

  // Our real catalogue (all cards incl. excluded — the toggle curates which are actual).
  useEffect(() => {
    api<{ cards: Card[] }>("/api/wb-cab/cards?limit=5000&include_excluded=1")
      .then((d) => setArticles(d.cards || []))
      .catch(() => setArticles([]))
      .finally(() => setLoading(false));
  }, []);

  // Persist immediately so the flag is shared between the Артикулы and План продаж tabs.
  const toggleExclude = (nm_id: number) => {
    setArticles((prev) => prev.map((a) => (a.nm_id === nm_id ? { ...a, excluded: !a.excluded } : a)));
    const next = !articles.find((a) => a.nm_id === nm_id)?.excluded;
    apiPost("/api/wb-cab/cards/exclude", { nm_id, excluded: next }).catch(() => {
      // revert on failure
      setArticles((prev) => prev.map((a) => (a.nm_id === nm_id ? { ...a, excluded: !a.excluded } : a)));
    });
  };

  return (
    <div className="w-full min-w-0">
      <div className="flex w-full flex-col gap-5 md:gap-6">
        <section className="overflow-hidden rounded-3xl border border-slate-200 bg-gradient-to-br from-white via-white to-brand-50/60 p-5 shadow-sm md:p-7">
          <div className="flex flex-col gap-5 2xl:flex-row 2xl:items-end 2xl:justify-between">
            <div className="max-w-2xl">
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-brand-100 bg-brand-50 px-3 py-1 text-[11px] font-bold uppercase tracking-[0.14em] text-brand-600">
                Управление WB-кабинетом
              </div>
              <h1 className="text-3xl font-black tracking-tight text-slate-900 md:text-4xl">Настройка</h1>
              <p className="mt-2 text-sm font-medium leading-6 text-slate-500 md:text-base">
                Управляйте каталогом, себестоимостью и планами продаж в одном рабочем пространстве.
              </p>
            </div>

            <nav className="grid w-full grid-cols-1 gap-3 sm:grid-cols-2 2xl:w-auto" aria-label="Разделы настройки">
              {SETTINGS_TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={cn(
                      "group flex min-h-20 w-full items-center gap-3 rounded-2xl border p-3.5 text-left transition-all 2xl:min-w-72",
                      isActive
                        ? "border-brand-200 bg-white text-slate-900 shadow-md shadow-brand-500/10"
                        : "border-white/70 bg-white/60 text-slate-700 hover:border-slate-200 hover:bg-white hover:shadow-sm"
                    )}
                  >
                    <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl transition-colors", isActive ? "bg-brand-600 text-white shadow-sm shadow-brand-500/30" : "bg-slate-100 text-slate-500 group-hover:bg-brand-50 group-hover:text-brand-600")}>
                      <Icon className="w-5 h-5" />
                    </div>
                    <div className="min-w-0">
                      <div className="mb-1 text-sm font-bold">{tab.label}</div>
                      <div className="text-xs font-medium leading-relaxed text-slate-500">{tab.description}</div>
                    </div>
                  </button>
                );
              })}
            </nav>
          </div>
        </section>

        <div className="w-full min-w-0 overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-[0_24px_70px_-42px_rgba(15,23,42,0.45)]">
          {activeTab === 'articles' && <ArticlesSettings articles={articles} loading={loading} toggleExclude={toggleExclude} />}
          {activeTab === 'sales_plan' && <SalesPlanSettings articles={articles.filter((a) => !a.excluded)} loading={loading} />}
        </div>
      </div>
    </div>
  );
}

const WEEKS = ['29.06-05.07', '06.07-12.07', '13.07-19.07', '20.07-26.07'];

const handleGridPaste = (e: ClipboardEvent<HTMLInputElement>, startRowIdx: number, startColIdx: number, gridPrefix: string) => {
  e.preventDefault();
  const pasteData = e.clipboardData.getData('text');
  if (!pasteData) return;
  const rows = pasteData.split(/\r?\n/).map((r) => r.split('\t'));
  rows.forEach((row, rOffset) => {
    row.forEach((cellData, cOffset) => {
      if (cellData === undefined) return;
      const targetInput = document.querySelector(`input[data-grid="${gridPrefix}"][data-row="${startRowIdx + rOffset}"][data-col="${startColIdx + cOffset}"]`) as HTMLInputElement;
      if (targetInput && !targetInput.disabled) targetInput.value = cellData.trim();
    });
  });
};

function ArticlePhoto({ url }: { url: string | null }) {
  return (
    <div className="w-10 h-10 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-400 shrink-0 overflow-hidden">
      {url ? <img src={url} alt="" className="w-full h-full object-cover" /> : <Package className="w-5 h-5 opacity-50" />}
    </div>
  );
}

function LoadingBlock() {
  return (
    <div className="p-16 flex items-center justify-center text-slate-400 gap-3">
      <Loader2 className="w-5 h-5 animate-spin" /> <span className="text-sm font-bold">Загружаю каталог…</span>
    </div>
  );
}

function ArticlesSettings({ articles, loading, toggleExclude }: { articles: Card[]; loading: boolean; toggleExclude: (nm: number) => void }) {
  const activeCount = articles.filter((a) => !a.excluded).length;
  return (
    <div className="flex flex-col gap-6 p-5 animate-in fade-in zoom-in-95 duration-300 md:p-7">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900">Артикулы и себестоимость</h2>
          <p className="text-sm font-medium text-slate-500 mt-1">
            Заполнение себестоимости и исключение неактуальных товаров
            {!loading && <span className="text-slate-400"> · всего {articles.length}, активных {activeCount}</span>}
          </p>
        </div>
        <button className="w-full sm:w-auto flex items-center justify-center gap-2 bg-brand-600 text-white px-5 py-2.5 rounded-xl text-sm font-bold shadow-md shadow-brand-500/20 hover:bg-brand-700 transition-colors">
          <Save className="w-4 h-4" />
          Сохранить изменения
        </button>
      </div>
      {loading ? <LoadingBlock /> : articles.length === 0 ? (
        <div className="py-12 text-center text-sm font-bold text-slate-400">Каталог пуст — товары появятся после синхронизации с WB.</div>
      ) : (
        <div className="mt-1 overflow-hidden rounded-2xl border border-slate-200 shadow-sm">
          <div className="max-h-[70vh] overflow-auto">
            <table className="min-w-[980px] w-full text-left whitespace-nowrap">
              <thead className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-20">
                <tr>
                  <th className="px-5 py-4 text-[10px] font-bold text-slate-400 uppercase tracking-wider sticky left-0 bg-white z-10 w-64 border-r border-slate-100 shadow-[1px_0_0_0_#f1f5f9]">Артикул</th>
                  <th className="px-5 py-4 text-[10px] font-bold text-slate-400 uppercase tracking-wider min-w-[120px]">Бренд</th>
                  {WEEKS.map((week) => (
                    <th key={week} className="px-5 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-wider text-right min-w-[120px]">{week}</th>
                  ))}
                  <th className="px-5 py-4 text-[10px] font-bold text-slate-400 uppercase tracking-wider text-right sticky right-0 bg-white z-10 border-l border-slate-100 w-24 shadow-[-1px_0_0_0_#f1f5f9]">Статус</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {articles.map((a) => (
                  <tr key={a.nm_id} className={cn("transition-colors group", a.excluded ? "bg-slate-50/50 grayscale opacity-60" : "hover:bg-slate-50")}>
                    <td className={cn("px-5 py-3 sticky left-0 border-r border-slate-100 z-10 transition-colors", a.excluded ? "bg-slate-50" : "bg-white group-hover:bg-slate-50")}>
                      <div className="flex items-center gap-3">
                        <ArticlePhoto url={a.photo_url} />
                        <div className="min-w-0 w-44">
                          <div className={cn("font-bold text-sm truncate", a.excluded ? "text-slate-500 line-through" : "text-slate-900")}>{a.vendor_code || a.nm_id}</div>
                          <div className="text-xs text-slate-500 font-medium truncate">{a.title || a.subject_name || ''}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-sm font-bold text-slate-600">{a.brand || '—'}</td>
                    {WEEKS.map((_, idx) => (
                      <td key={idx} className="px-5 py-3 text-right">
                        <input
                          type="text"
                          placeholder="—"
                          disabled={a.excluded}
                          data-grid="articles"
                          data-row={a.nm_id}
                          data-col={idx}
                          onPaste={(e) => handleGridPaste(e, a.nm_id, idx, 'articles')}
                          className="w-24 text-right bg-transparent hover:bg-white focus:bg-white border border-transparent hover:border-slate-200 rounded-lg px-3 py-1.5 text-sm font-bold text-slate-900 placeholder-slate-300 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-all disabled:opacity-50 disabled:bg-transparent"
                        />
                      </td>
                    ))}
                    <td className={cn("px-5 py-3 sticky right-0 border-l border-slate-100 z-10 text-right transition-colors", a.excluded ? "bg-slate-50" : "bg-white group-hover:bg-slate-50")}>
                      <button
                        onClick={() => toggleExclude(a.nm_id)}
                        className={cn(
                          "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors shadow-sm border",
                          a.excluded ? "bg-slate-100 text-slate-600 border-slate-200 hover:bg-slate-200" : "bg-red-50 text-red-600 border-red-100 hover:bg-red-100"
                        )}
                      >
                        {a.excluded ? (<><Eye className="w-3.5 h-3.5" /> Вернуть</>) : (<><EyeOff className="w-3.5 h-3.5" /> Исключить</>)}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function getMonthsInRange(start: string, end: string) {
  if (!start || !end) return [];
  const startDate = new Date(start + '-01');
  const endDate = new Date(end + '-01');
  if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) return [];
  const months: string[] = [];
  let current = new Date(startDate.getFullYear(), startDate.getMonth(), 1);
  while (current <= endDate) {
    const monthName = current.toLocaleString('ru-RU', { month: 'long' });
    months.push(`${monthName.charAt(0).toUpperCase() + monthName.slice(1)} ${current.getFullYear()}`);
    current.setMonth(current.getMonth() + 1);
  }
  return months;
}

function CustomMonthPicker({ value, onChange, label }: { value: string, onChange: (val: string) => void, label: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const [viewYear, setViewYear] = useState(parseInt(value.split('-')[0]) || 2026);
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setIsOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);
  const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const displayValue = () => {
    if (!value) return '';
    const [y, m] = value.split('-');
    return `${monthNames[parseInt(m) - 1].slice(0, 3)} ${y}`;
  };
  return (
    <div className="relative flex items-center gap-2 px-3 py-1.5" ref={ref}>
      <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">{label}</span>
      <div className="flex items-center gap-1 cursor-pointer select-none" onClick={() => setIsOpen(!isOpen)}>
        <span className="text-sm font-bold text-slate-900">{displayValue()}</span>
        <ChevronDown className={cn("w-3 h-3 text-slate-400 transition-transform", isOpen && "rotate-180")} />
      </div>
      {isOpen && (
        <div className="absolute top-full right-0 mt-2 p-3 bg-white border border-slate-200 rounded-2xl shadow-xl shadow-slate-200/50 z-50 animate-in fade-in zoom-in-95 duration-200 w-56">
          <div className="flex items-center justify-between mb-3 px-1">
            <button onClick={() => setViewYear((y) => y - 1)} className="p-1 hover:bg-slate-100 rounded-md transition-colors text-slate-500 hover:text-slate-900"><ChevronLeft className="w-4 h-4" /></button>
            <div className="font-bold text-slate-900 text-sm">{viewYear}</div>
            <button onClick={() => setViewYear((y) => y + 1)} className="p-1 hover:bg-slate-100 rounded-md transition-colors text-slate-500 hover:text-slate-900"><ChevronRight className="w-4 h-4" /></button>
          </div>
          <div className="grid grid-cols-3 gap-1">
            {monthNames.map((month, idx) => {
              const monthVal = `${viewYear}-${String(idx + 1).padStart(2, '0')}`;
              const isSelected = value === monthVal;
              return (
                <button key={month} onClick={() => { onChange(monthVal); setIsOpen(false); }}
                  className={cn("text-xs font-medium py-2 rounded-lg transition-colors", isSelected ? "bg-brand-600 text-white shadow-sm shadow-brand-500/20 font-bold" : "text-slate-700 hover:bg-slate-100")}>
                  {month.slice(0, 3)}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function SalesPlanSettings({ articles, loading }: { articles: Card[], loading: boolean }) {
  const [periodStart, setPeriodStart] = useState('2026-07');
  const [periodEnd, setPeriodEnd] = useState('2026-09');
  const months = getMonthsInRange(periodStart, periodEnd);

  return (
    <div className="flex flex-col gap-6 p-5 animate-in fade-in zoom-in-95 duration-300 md:p-7">
      <div className="flex flex-col xl:flex-row items-start xl:items-center justify-between gap-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900">План продаж</h2>
          <p className="text-sm font-medium text-slate-500 mt-1">
            Детальный план продаж по каждому артикулу на выбранный период
            {!loading && <span className="text-slate-400"> · активных товаров {articles.length}</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 w-full xl:w-auto">
          <div className="flex items-center bg-slate-50 border border-slate-200 rounded-xl p-1 shadow-sm shrink-0">
            <CustomMonthPicker label="Период с" value={periodStart} onChange={setPeriodStart} />
            <div className="w-px h-6 bg-slate-200"></div>
            <CustomMonthPicker label="по" value={periodEnd} onChange={setPeriodEnd} />
          </div>
          <button className="flex-1 sm:flex-none flex items-center justify-center gap-2 bg-brand-600 text-white px-6 py-2.5 rounded-xl text-sm font-bold shadow-md shadow-brand-500/20 hover:bg-brand-700 transition-colors">
            <Save className="w-4 h-4" />
            Сохранить план
          </button>
        </div>
      </div>

      {loading ? <LoadingBlock /> : months.length === 0 ? (
        <div className="py-12 flex flex-col items-center justify-center text-slate-500 border border-dashed border-slate-200 rounded-2xl bg-slate-50">
          <CalendarIcon className="w-12 h-12 text-slate-300 mb-3" />
          <div className="text-sm font-bold">Выберите корректный период</div>
        </div>
      ) : articles.length === 0 ? (
        <div className="py-12 text-center text-sm font-bold text-slate-400">Нет активных товаров — все исключены или каталог ещё пуст.</div>
      ) : (
        <div className="mt-1 overflow-hidden rounded-2xl border border-slate-200 shadow-sm">
          <div className="max-h-[70vh] overflow-auto">
            <table className="min-w-max w-full text-left whitespace-nowrap">
              <thead className="bg-white sticky top-0 z-20">
                <tr>
                  <th colSpan={2} className="px-5 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-wider sticky left-0 bg-white z-20 border-r border-slate-100">Товар</th>
                  {months.map((month) => (
                    <th key={month} colSpan={3} className="px-5 py-4 text-xs font-black text-slate-900 tracking-wide text-center border-r border-slate-100 last:border-r-0 bg-slate-50/50">{month}</th>
                  ))}
                </tr>
                <tr className="border-b border-slate-200 shadow-sm">
                  <th className="px-5 py-3 text-[10px] font-bold text-slate-500 uppercase tracking-wider sticky left-0 bg-white z-20 border-r border-slate-100 w-64">Артикул</th>
                  <th className="px-5 py-3 text-[10px] font-bold text-slate-500 uppercase tracking-wider sticky left-64 bg-white z-20 border-r border-slate-100 shadow-[1px_0_0_0_#f1f5f9]">Бренд</th>
                  {months.map((month) => (
                    <Fragment key={month + '-metrics'}>
                      <th className="px-4 py-3 text-[10px] font-bold text-slate-500 uppercase tracking-wider text-right border-l border-slate-100 bg-white">План руб.</th>
                      <th className="px-4 py-3 text-[10px] font-bold text-slate-500 uppercase tracking-wider text-right bg-white">План цена, руб</th>
                      <th className="px-4 py-3 text-[10px] font-bold text-slate-500 uppercase tracking-wider text-right border-r border-slate-100 bg-white">План в месяц (шт)</th>
                    </Fragment>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {articles.map((a) => (
                  <tr key={a.nm_id} className="hover:bg-slate-50 transition-colors group">
                    <td className="px-5 py-3 sticky left-0 bg-white group-hover:bg-slate-50 border-r border-slate-100 z-10">
                      <div className="flex items-center gap-3">
                        <ArticlePhoto url={a.photo_url} />
                        <div className="min-w-0 w-48">
                          <div className="font-bold text-slate-900 text-sm truncate">{a.vendor_code || a.nm_id}</div>
                          <div className="text-xs text-slate-500 font-medium truncate">{a.title || a.subject_name || ''}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-sm font-bold text-slate-600 sticky left-64 bg-white group-hover:bg-slate-50 border-r border-slate-100 z-10 shadow-[1px_0_0_0_#f1f5f9]">{a.brand || '—'}</td>
                    {months.map((month, monthIdx) => (
                      <Fragment key={month + '-' + a.nm_id}>
                        <td className="px-4 py-3 text-right border-l border-slate-100">
                          <input type="text" placeholder="—" data-grid="sales" data-row={a.nm_id} data-col={monthIdx * 3} onPaste={(e) => handleGridPaste(e, a.nm_id, monthIdx * 3, 'sales')}
                            className="w-28 text-right bg-transparent hover:bg-white focus:bg-white border border-transparent hover:border-slate-200 rounded-lg px-2.5 py-1.5 text-sm font-bold text-slate-900 placeholder-slate-300 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-all" />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <input type="text" placeholder="—" data-grid="sales" data-row={a.nm_id} data-col={monthIdx * 3 + 1} onPaste={(e) => handleGridPaste(e, a.nm_id, monthIdx * 3 + 1, 'sales')}
                            className="w-24 text-right bg-transparent hover:bg-white focus:bg-white border border-transparent hover:border-slate-200 rounded-lg px-2.5 py-1.5 text-sm font-bold text-slate-900 placeholder-slate-300 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-all" />
                        </td>
                        <td className="px-4 py-3 text-right border-r border-slate-100 bg-slate-50/30">
                          <input type="text" placeholder="—" data-grid="sales" data-row={a.nm_id} data-col={monthIdx * 3 + 2} onPaste={(e) => handleGridPaste(e, a.nm_id, monthIdx * 3 + 2, 'sales')}
                            className="w-20 text-right bg-transparent hover:bg-white focus:bg-white border border-transparent hover:border-slate-200 rounded-lg px-2.5 py-1.5 text-sm font-bold text-slate-900 placeholder-slate-300 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition-all" />
                        </td>
                      </Fragment>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
