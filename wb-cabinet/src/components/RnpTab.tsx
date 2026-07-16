import { useState, useRef, useEffect } from 'react';
import { Search, Image as ImageIcon, BarChart3 } from 'lucide-react';
import { cn } from '../lib/utils';
import { api, qs } from '../lib/api';

type Card = { nm_id: number; vendor_code: string | null; title: string | null; brand: string | null; subject_name: string | null; photo_url: string | null };

export function RnpTab({ brand }: { brand?: string }) {
  const [search, setSearch] = useState('');
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Card | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Real catalogue from our DB (wb_cards), filtered by search + the page brand filter.
  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      api<{ cards: Card[] }>(`/api/wb-cab/cards${qs({ q: search, brand: brand === 'Все' ? undefined : brand })}`)
        .then((d) => setCards(d.cards || []))
        .catch(() => setCards([]))
        .finally(() => setLoading(false));
    }, 220);
    return () => clearTimeout(t);
  }, [search, brand]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) setIsSearching(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="flex flex-col gap-6 w-full max-w-full">
      {/* Smart Search */}
      <div className="bg-white p-6 md:p-8 rounded-2xl shadow-sm border border-slate-100 flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900 mb-1">Аналитика по артикулу (РНП)</h2>
          <p className="text-sm font-medium text-slate-500">Выберите наш товар из каталога для детального анализа метрик</p>
        </div>

        <div className="relative max-w-xl" ref={searchRef}>
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <Search className="h-5 w-5 text-slate-400" />
          </div>
          <input
            type="text"
            className="block w-full pl-11 pr-4 py-3.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm"
            placeholder="Введите артикул или название товара…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setIsSearching(true); }}
            onFocus={() => setIsSearching(true)}
          />
          {isSearching && (
            <div className="absolute z-50 w-full mt-2 bg-white border border-slate-200 rounded-2xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
              {loading ? (
                <div className="px-4 py-8 text-center text-sm text-slate-500 font-medium">Загружаю каталог…</div>
              ) : cards.length > 0 ? (
                <ul className="max-h-80 overflow-auto divide-y divide-slate-100">
                  {cards.map((c) => (
                    <li key={c.nm_id}>
                      <button
                        className="w-full flex items-center gap-4 px-4 py-3 hover:bg-slate-50 transition-colors text-left"
                        onClick={() => { setSelected(c); setSearch(c.vendor_code || String(c.nm_id)); setIsSearching(false); }}
                      >
                        <div className="w-12 h-12 rounded-lg overflow-hidden shrink-0 border border-slate-200 bg-white shadow-sm flex items-center justify-center">
                          {c.photo_url ? (
                            <img src={c.photo_url} alt={c.title || ''} className="w-full h-full object-cover" />
                          ) : (
                            <ImageIcon className="w-5 h-5 text-slate-400" />
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="font-bold text-slate-900 mb-0.5 truncate">{c.vendor_code || c.nm_id}</div>
                          <div className="text-xs text-slate-500 font-medium line-clamp-1">{c.title || c.subject_name || ''}</div>
                        </div>
                        {c.brand && <span className="ml-auto text-[10px] font-bold text-slate-400 uppercase shrink-0">{c.brand}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="px-4 py-8 text-center flex flex-col items-center justify-center gap-2">
                  <Search className="w-8 h-8 text-slate-300" />
                  <div className="text-sm text-slate-500 font-medium">Ничего не найдено</div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Selected Article Info */}
        {selected && (
          <div className="flex items-center gap-6 pt-6 border-t border-slate-100 mt-2 animate-in fade-in duration-500">
            <div className="w-28 h-28 rounded-2xl overflow-hidden shrink-0 border border-slate-200 shadow-md bg-white flex items-center justify-center p-1">
              <div className="w-full h-full rounded-xl overflow-hidden bg-slate-50 flex items-center justify-center">
                {selected.photo_url ? (
                  <img src={selected.photo_url} alt={selected.title || ''} className="w-full h-full object-cover" />
                ) : (
                  <ImageIcon className="w-8 h-8 text-slate-300" />
                )}
              </div>
            </div>
            <div className="min-w-0">
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-brand-50 text-brand-600 text-[10px] font-bold uppercase tracking-wider mb-3">
                Выбранный товар
              </div>
              <div className="text-3xl font-black text-slate-900 mb-1.5 tracking-tight">{selected.vendor_code || selected.nm_id}</div>
              <div className="text-sm font-medium text-slate-500 max-w-md">{selected.title || selected.subject_name || ''}</div>
              <div className="text-xs font-medium text-slate-400 mt-1">nm_id {selected.nm_id}{selected.brand ? ` · ${selected.brand}` : ''}</div>
            </div>
          </div>
        )}
      </div>

      {/* Metrics — plan/fact tables need the plan & cost logic we build next; empty for now */}
      {selected && (
        <div className={cn(
          "bg-white p-10 rounded-2xl shadow-sm border border-slate-100 flex flex-col items-center justify-center text-center",
          "animate-in slide-in-from-bottom-4 fade-in duration-500"
        )}>
          <div className="w-16 h-16 rounded-2xl bg-brand-50 text-brand-500 flex items-center justify-center mb-4">
            <BarChart3 className="w-8 h-8" />
          </div>
          <h3 className="text-lg font-bold text-slate-900 mb-1">Метрики по артикулу появятся здесь</h3>
          <p className="text-sm font-medium text-slate-500 max-w-md">
            Товар <span className="font-bold text-slate-700">{selected.vendor_code || selected.nm_id}</span> выбран из нашего каталога.
            План/факт, продажи и рентабельность заполнятся после загрузки статистики WB и настройки плана продаж по этому артикулу.
          </p>
        </div>
      )}
    </div>
  );
}
