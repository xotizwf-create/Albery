import React, { useEffect, useMemo, useState } from "react";

/** WB-кабинет: аналитика Wildberries. Разделы: Общий дашборд · РНП · ОПиУ · ДДС ·
 * По артикулам · Налоговый калькулятор. Данные: /api/wb-cab/* (читают ТОЛЬКО нашу БД,
 * которую наполняет wb_sync). Фильтр по бренду + период — общие для всех разделов. */

const ACCENT = "#5440F6";
const SECTIONS = ["Общий дашборд", "РНП", "ОПиУ", "ДДС", "По артикулам", "Налоговый калькулятор"] as const;
type Section = (typeof SECTIONS)[number];

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  return Math.round(Number(v)).toLocaleString("ru-RU");
}
function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  return Number(v).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}
function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

const card = "rounded-2xl border border-[#E6EAF2] bg-white p-5 shadow-[0_18px_50px_-38px_rgba(15,23,42,0.45)]";
const label = "text-[12px] font-bold uppercase tracking-wide text-slate-400";
const h2 = "text-[15px] font-black text-slate-900";
const thCls = "px-3 py-2 text-left text-[11px] font-black uppercase tracking-wide text-slate-400 whitespace-nowrap";
const tdCls = "px-3 py-2 text-[13px] font-semibold text-slate-700 whitespace-nowrap";

function Spark({ values, width = 110, height = 30 }: { values: number[]; width?: number; height?: number }) {
  if (!values || values.length < 2) return <span className="text-slate-300">·</span>;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - 4 - ((v - min) / span) * (height - 8)}`).join(" ");
  return (
    <svg width={width} height={height} className="block">
      <polyline points={pts} fill="none" stroke={ACCENT} strokeWidth={1.6} />
    </svg>
  );
}

export function WbCabinet() {
  const [section, setSection] = useState<Section>("Общий дашборд");
  const [dFrom, setDFrom] = useState(isoDaysAgo(29));
  const [dTo, setDTo] = useState(isoDaysAgo(0));
  const [brand, setBrand] = useState("");
  const [brands, setBrands] = useState<string[]>([]);
  const [syncInfo, setSyncInfo] = useState<any>(null);

  useEffect(() => {
    getJson<{ brands: string[] }>("/api/wb-cab/brands").then((d) => setBrands(d.brands || [])).catch(() => {});
    getJson<any>("/api/wb-cab/sync-status").then(setSyncInfo).catch(() => {});
  }, []);

  const qs = useMemo(() => {
    const p = new URLSearchParams({ from: dFrom, to: dTo });
    if (brand) p.set("brand", brand);
    return p.toString();
  }, [dFrom, dTo, brand]);

  return (
    <div className="animate-in fade-in duration-200">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 rounded-xl border border-[#E6EAF2] bg-white px-3 py-2">
          <span className={label}>Период</span>
          <input type="date" value={dFrom} onChange={(e) => setDFrom(e.target.value)}
                 className="bg-transparent text-[13px] font-bold text-slate-800 outline-none" />
          <span className="text-slate-300">—</span>
          <input type="date" value={dTo} onChange={(e) => setDTo(e.target.value)}
                 className="bg-transparent text-[13px] font-bold text-slate-800 outline-none" />
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-[#E6EAF2] bg-white px-3 py-2">
          <span className={label}>Бренд</span>
          <select value={brand} onChange={(e) => setBrand(e.target.value)}
                  className="bg-transparent text-[13px] font-bold text-slate-800 outline-none">
            <option value="">Все</option>
            {brands.map((b) => (<option key={b} value={b}>{b}</option>))}
          </select>
        </div>
        {syncInfo?.counts && (
          <span className="ml-auto text-[12px] font-semibold text-slate-400">
            в базе: заказов {fmtNum(syncInfo.counts.orders)} · выкупов {fmtNum(syncInfo.counts.sales)} ·
            финстрок {fmtNum(syncInfo.counts.finance)}
          </span>
        )}
      </div>

      <div className="flex gap-5">
        <div className="w-56 shrink-0 space-y-2">
          {SECTIONS.map((s) => (
            <button key={s} onClick={() => setSection(s)}
              className={`w-full rounded-xl border px-4 py-3 text-left text-[13px] font-black transition-all ${
                section === s
                  ? "border-[#5440F6] bg-[#5440F6]/5 text-[#5440F6]"
                  : "border-[#E6EAF2] bg-white text-slate-600 hover:border-slate-300"
              }`}>
              {s}
            </button>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          {section === "Общий дашборд" && <Dashboard qs={qs} />}
          {section === "РНП" && <Rnp qs={qs} />}
          {section === "ОПиУ" && <Pnl qs={qs} />}
          {section === "ДДС" && <Cashflow qs={qs} />}
          {section === "По артикулам" && <Articles qs={qs} dFrom={dFrom} dTo={dTo} />}
          {section === "Налоговый калькулятор" && <TaxCalc qs={qs} />}
        </div>
      </div>
    </div>
  );
}

function useData<T>(url: string): { data: T | null; err: string; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true); setErr("");
    getJson<T>(url).then((d) => { if (alive) { setData(d); setLoading(false); } })
      .catch((e) => { if (alive) { setErr(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [url]);
  return { data, err, loading };
}

function Empty({ loading, err }: { loading: boolean; err: string }) {
  return (
    <div className={`${card} flex min-h-[220px] items-center justify-center text-[14px] font-bold text-slate-400`}>
      {loading ? "Загружаю…" : err ? `Ошибка: ${err}` : "Нет данных за период — дождитесь синхронизации"}
    </div>
  );
}

/* ------------------------------------------------ Общий дашборд */
function Dashboard({ qs }: { qs: string }) {
  const { data, err, loading } = useData<any>(`/api/wb-cab/summary?${qs}`);
  if (!data) return <Empty loading={loading} err={err} />;
  const o = data.orders || {}, s = data.sales || {}, st = data.stocks || {};
  const daily: any[] = data.daily || [];
  const maxRub = Math.max(1, ...daily.map((d) => Number(d.orders_rub) || 0));
  const kpi = [
    { t: "Заказы", v: `${fmtNum(o.orders_cnt)} шт`, sub: `${fmtMoney(o.orders_rub)} ₽` },
    { t: "Выкупы", v: `${fmtNum(s.sales_cnt)} шт`, sub: `${fmtMoney(s.sales_rub)} ₽` },
    { t: "Возвраты", v: `${fmtNum(s.returns_cnt)} шт`, sub: `${fmtMoney(s.returns_rub)} ₽` },
    { t: "К перечислению", v: `${fmtMoney(s.for_pay_rub)} ₽`, sub: "по выкупам периода" },
    { t: "Остатки на складах", v: `${fmtNum(st.stock_qty)} шт`, sub: `в пути к клиенту ${fmtNum(st.in_way_to)} · от клиента ${fmtNum(st.in_way_from)}` },
  ];
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
        {kpi.map((k) => (
          <div key={k.t} className={card}>
            <div className={label}>{k.t}</div>
            <div className="mt-1 text-[22px] font-black tracking-tight text-slate-950">{k.v}</div>
            <div className="mt-1 text-[12px] font-semibold text-slate-400">{k.sub}</div>
          </div>
        ))}
      </div>
      <div className={card}>
        <div className={h2}>Заказы по дням, ₽</div>
        <div className="mt-4 flex h-40 items-end gap-[3px]">
          {daily.map((d) => (
            <div key={d.day} title={`${d.day}: ${fmtMoney(d.orders_rub)} ₽ (${d.orders_cnt} шт)`}
                 className="flex-1 rounded-t-md bg-[#5440F6]/80 transition-all hover:bg-[#5440F6]"
                 style={{ height: `${Math.max(3, (Number(d.orders_rub) / maxRub) * 100)}%` }} />
          ))}
        </div>
        {daily.length > 0 && (
          <div className="mt-2 flex justify-between text-[11px] font-bold text-slate-400">
            <span>{daily[0].day}</span><span>{daily[daily.length - 1].day}</span>
          </div>
        )}
      </div>
      <div className={card}>
        <div className={h2}>Топ-10 артикулов по заказам</div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full">
            <thead><tr className="border-b border-[#E6EAF2]">
              <th className={thCls}>Фото</th><th className={thCls}>Артикул</th><th className={thCls}>Название</th>
              <th className={thCls}>Заказы, шт</th><th className={thCls}>Заказы, ₽</th>
            </tr></thead>
            <tbody>
              {(data.top_articles || []).map((t: any) => (
                <tr key={t.nm_id} className="border-b border-[#F1F3F9] last:border-0">
                  <td className={tdCls}>{t.photo ? <img src={t.photo} alt="" className="h-10 w-8 rounded-md object-cover" /> : <span className="text-slate-300">нет</span>}</td>
                  <td className={tdCls}><span className="font-black text-slate-900">{t.article || t.nm_id}</span><div className="text-[11px] text-slate-400">{t.nm_id}</div></td>
                  <td className={`${tdCls} max-w-[320px] truncate`}>{t.title || "—"}</td>
                  <td className={tdCls}>{fmtNum(t.orders_cnt)}</td>
                  <td className={`${tdCls} font-black text-slate-900`}>{fmtMoney(t.orders_rub)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------ РНП (Рука на пульсе) */
function Rnp({ qs }: { qs: string }) {
  const { data, err, loading } = useData<any>(`/api/wb-cab/rnp?${qs}`);
  const days: any[] = data?.days || [];
  if (!days.length) return <Empty loading={loading} err={err} />;
  return (
    <div className={card}>
      <div className={h2}>РНП — день к дню</div>
      <div className="mt-3 max-h-[620px] overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-white"><tr className="border-b border-[#E6EAF2]">
            {["День", "Заказы, шт", "Заказы, ₽", "Выкупы, шт", "Выкупы, ₽", "Возвраты", "Остаток, шт", "Реклама, ₽", "ДРР, %"].map((h) => <th key={h} className={thCls}>{h}</th>)}
          </tr></thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.day} className="border-b border-[#F1F3F9] last:border-0 hover:bg-slate-50/60">
                <td className={`${tdCls} font-black text-slate-900`}>{d.day}</td>
                <td className={tdCls}>{fmtNum(d.orders_cnt)}</td>
                <td className={tdCls}>{fmtMoney(d.orders_rub)}</td>
                <td className={tdCls}>{fmtNum(d.sales_cnt)}</td>
                <td className={tdCls}>{fmtMoney(d.sales_rub)}</td>
                <td className={tdCls}>{fmtNum(d.returns_cnt)}</td>
                <td className={tdCls}>{fmtNum(d.stock_qty)}</td>
                <td className={tdCls}>{fmtMoney(d.adv_rub)}</td>
                <td className={`${tdCls} ${Number(d.drr_pct) > 15 ? "text-rose-600 font-black" : ""}`}>{fmtNum(d.drr_pct, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[11px] font-semibold text-slate-400">
        Метрики расширяемы: новые колонки добавляются запросом в БД без перевыгрузки данных.
      </div>
    </div>
  );
}

/* ------------------------------------------------ ОПиУ */
function Pnl({ qs }: { qs: string }) {
  const { data, err, loading } = useData<any>(`/api/wb-cab/pnl?${qs}`);
  if (!data?.total) return <Empty loading={loading} err={err} />;
  const t = data.total;
  const rows = [
    ["Выручка (реализация)", t.revenue],
    ["Комиссия WB", -t.commission],
    ["Логистика", -t.logistics],
    ["Хранение", -t.storage],
    ["Штрафы и удержания", -t.penalties],
    ["Реклама", -t.adv],
    ["Себестоимость продаж", -t.cogs],
  ];
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className={card}>
          <div className={h2}>ОПиУ за период</div>
          <table className="mt-3 w-full">
            <tbody>
              {rows.map(([n, v]) => (
                <tr key={String(n)} className="border-b border-[#F1F3F9] last:border-0">
                  <td className={tdCls}>{n}</td>
                  <td className={`${tdCls} text-right font-black ${Number(v) < 0 ? "text-rose-600" : "text-slate-900"}`}>{fmtMoney(Number(v))} ₽</td>
                </tr>
              ))}
              <tr>
                <td className={`${tdCls} font-black text-slate-900`}>Операционная прибыль</td>
                <td className={`${tdCls} text-right text-[16px] font-black ${t.operating_profit < 0 ? "text-rose-600" : "text-emerald-600"}`}>{fmtMoney(t.operating_profit)} ₽</td>
              </tr>
            </tbody>
          </table>
          {t.cogs_missing_barcodes > 0 && (
            <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[12px] font-bold text-amber-700">
              ⚠ Для {t.cogs_missing_barcodes} баркодов нет себестоимости — внесите её в разделе «Налоговый калькулятор» (или через Excel-шаблон), иначе прибыль завышена.
            </div>
          )}
        </div>
        <div className={card}>
          <div className={h2}>По месяцам</div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full">
              <thead><tr className="border-b border-[#E6EAF2]">
                {["Месяц", "Выручка", "К перечислению", "Логистика", "Хранение", "Штрафы"].map((h) => <th key={h} className={thCls}>{h}</th>)}
              </tr></thead>
              <tbody>
                {(data.months || []).map((m: any) => (
                  <tr key={m.month} className="border-b border-[#F1F3F9] last:border-0">
                    <td className={`${tdCls} font-black text-slate-900`}>{m.month}</td>
                    <td className={tdCls}>{fmtMoney(m.revenue)}</td>
                    <td className={tdCls}>{fmtMoney(m.payout)}</td>
                    <td className={tdCls}>{fmtMoney(m.logistics)}</td>
                    <td className={tdCls}>{fmtMoney(m.storage)}</td>
                    <td className={tdCls}>{fmtMoney(m.penalties)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------ ДДС */
function Cashflow({ qs }: { qs: string }) {
  const { data, err, loading } = useData<any>(`/api/wb-cab/cashflow?${qs}`);
  const rows: any[] = data?.reports || [];
  if (!rows.length) return <Empty loading={loading} err={err} />;
  return (
    <div className={card}>
      <div className={h2}>ДДС — еженедельные отчёты WB</div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full">
          <thead><tr className="border-b border-[#E6EAF2]">
            {["Отчёт №", "Период", "К перечислению", "Логистика", "Хранение", "Удержания/штрафы", "Чистыми на счёт"].map((h) => <th key={h} className={thCls}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.realizationreport_id} className="border-b border-[#F1F3F9] last:border-0 hover:bg-slate-50/60">
                <td className={tdCls}>{r.realizationreport_id}</td>
                <td className={`${tdCls} font-black text-slate-900`}>{r.date_from} — {r.date_to}</td>
                <td className={tdCls}>{fmtMoney(r.payout)}</td>
                <td className={tdCls}>{fmtMoney(r.logistics)}</td>
                <td className={tdCls}>{fmtMoney(r.storage)}</td>
                <td className={tdCls}>{fmtMoney(r.deductions)}</td>
                <td className={`${tdCls} font-black ${Number(r.net_to_account) < 0 ? "text-rose-600" : "text-emerald-600"}`}>{fmtMoney(r.net_to_account)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------ По артикулам */
function Articles({ qs, dFrom, dTo }: { qs: string; dFrom: string; dTo: string }) {
  const { data, err, loading } = useData<any>(`/api/wb-cab/articles?${qs}`);
  const rows: any[] = data?.articles || [];
  if (!rows.length) return <Empty loading={loading} err={err} />;
  const lastDays: string[] = [];
  {
    const end = new Date(dTo);
    for (let i = 6; i >= 0; i--) {
      const d = new Date(end); d.setDate(end.getDate() - i);
      if (d >= new Date(dFrom)) lastDays.push(d.toISOString().slice(0, 10));
    }
  }
  const maxDay = Math.max(1, ...rows.flatMap((r) => lastDays.map((d) => Number(r.orders_by_day?.[d]) || 0)));
  return (
    <div className={card}>
      <div className={h2}>По артикулам</div>
      <div className="mt-3 max-h-[640px] overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 z-10 bg-white"><tr className="border-b border-[#E6EAF2]">
            <th className={thCls}>Фото</th><th className={thCls}>Артикул</th><th className={thCls}>Остаток</th>
            <th className={thCls}>Динамика остатков</th><th className={thCls}>Заказы, ₽</th>
            <th className={thCls}>Скорость (шт/день)</th>
            {lastDays.map((d) => <th key={d} className={thCls}>{d.slice(8, 10)}.{d.slice(5, 7)}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.nm_id} className="border-b border-[#F1F3F9] last:border-0 hover:bg-slate-50/60">
                <td className={tdCls}>{r.photo_url ? <img src={r.photo_url} alt="" className="h-11 w-9 rounded-md object-cover" /> : <span className="text-slate-300">нет фото</span>}</td>
                <td className={tdCls}>
                  <span className="font-black text-[#5440F6]">{r.vendor_code || r.nm_id}</span>
                  <div className="max-w-[220px] truncate text-[11px] text-slate-400">{r.title || r.subject_name || ""}</div>
                  <div className="text-[11px] text-slate-300">{r.nm_id} · {r.brand}</div>
                </td>
                <td className={`${tdCls} font-black text-slate-900`}>{fmtNum(r.stock_qty)}</td>
                <td className={tdCls}><Spark values={(r.stock_spark || []).map(Number)} /></td>
                <td className={`${tdCls} font-black text-slate-900`}>{fmtMoney(r.orders_rub)}</td>
                <td className={tdCls}>{fmtNum(r.orders_per_day, 1)}</td>
                {lastDays.map((d) => {
                  const v = Number(r.orders_by_day?.[d]) || 0;
                  const bg = v ? `rgba(84,64,246,${0.12 + 0.55 * (v / maxDay)})` : "transparent";
                  return <td key={d} className="px-2 py-2 text-center text-[12px] font-black" style={{ background: bg, color: v ? "#2a1e8f" : "#cbd5e1" }}>{v || "·"}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------ Налоговый калькулятор */
function TaxCalc({ qs }: { qs: string }) {
  const [mode, setMode] = useState("usn_d");
  const [rate, setRate] = useState<string>("6");
  const [vatMode, setVatMode] = useState("none");
  const [vatRefund, setVatRefund] = useState<string>("0");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    getJson<any>("/api/wb-cab/tax-settings").then((d) => {
      const c = d.current || {};
      if (c.mode) setMode(c.mode);
      if (c.rate !== undefined) setRate(String(c.rate));
      if (c.vat_mode) setVatMode(c.vat_mode);
      if (c.vat_refund_percent !== undefined) setVatRefund(String(c.vat_refund_percent));
    }).catch(() => {});
  }, []);

  const url = `/api/wb-cab/tax?${qs}&mode=${mode}&rate=${encodeURIComponent(rate)}&vat_mode=${vatMode}&vat_refund=${encodeURIComponent(vatRefund)}`;
  const { data, err, loading } = useData<any>(url);

  const MODES: [string, string][] = [["usn_d", "УСН Доходы"], ["usn_dr", "УСН Доходы-Расходы"], ["ausn_d", "АУСН Д"], ["ausn_dr", "АУСН Д-Р"], ["sng", "Страны СНГ"]];
  const VATS: [string, string][] = [["none", "Без НДС"], ["vat5", "НДС 5%"], ["vat7", "НДС 7%"], ["vat20", "НДС 20%"]];

  async function saveSettings() {
    setSaved("");
    try {
      await fetch("/api/wb-cab/tax-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, rate: Number(rate), vat_mode: vatMode, vat_refund_percent: Number(vatRefund) }),
      });
      setSaved("Сохранено ✓");
    } catch { setSaved("Ошибка сохранения"); }
  }

  return (
    <div className="space-y-5">
      <div className={card}>
        <div className={h2}>Налоговый режим</div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {MODES.map(([id, lbl]) => (
            <button key={id} onClick={() => setMode(id)}
              className={`rounded-xl border px-4 py-2 text-[13px] font-black transition-all ${
                mode === id ? "border-[#5440F6] bg-[#5440F6] text-white" : "border-[#E6EAF2] bg-white text-slate-600 hover:border-slate-300"}`}>
              {lbl}
            </button>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-4">
          <label className="block">
            <span className={label}>Ставка налога, %</span>
            <input value={rate} onChange={(e) => setRate(e.target.value)} type="number" step="0.1" min="0"
                   className="mt-1 block w-32 rounded-xl border border-[#E6EAF2] px-3 py-2 text-[14px] font-black text-slate-900 outline-none focus:border-[#5440F6]" />
          </label>
          <label className="block">
            <span className={label}>НДС</span>
            <select value={vatMode} onChange={(e) => setVatMode(e.target.value)}
                    className="mt-1 block w-40 rounded-xl border border-[#E6EAF2] px-3 py-2 text-[14px] font-black text-slate-900 outline-none focus:border-[#5440F6]">
              {VATS.map(([id, lbl]) => <option key={id} value={id}>{lbl}</option>)}
            </select>
          </label>
          <label className="block">
            <span className={label}>НДС к возмещению, %</span>
            <input value={vatRefund} onChange={(e) => setVatRefund(e.target.value)} type="number" step="1" min="0" max="100"
                   className="mt-1 block w-32 rounded-xl border border-[#E6EAF2] px-3 py-2 text-[14px] font-black text-slate-900 outline-none focus:border-[#5440F6]" />
          </label>
          <button onClick={saveSettings}
                  className="rounded-xl bg-[#5440F6] px-5 py-2.5 text-[13px] font-black text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95">
            Сохранить режим
          </button>
          {saved && <span className="text-[12px] font-bold text-emerald-600">{saved}</span>}
        </div>
      </div>

      {!data ? <Empty loading={loading} err={err} /> : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
          <div className={card}>
            <div className={label}>Реализация</div>
            <div className="mt-1 text-[24px] font-black tracking-tight text-slate-950">{fmtMoney(data.realization?.before_spp)}</div>
            <div className="mt-3 space-y-1 text-[12px] font-bold">
              <div className="flex justify-between"><span className="text-pink-500">● до СПП</span><span>{fmtMoney(data.realization?.before_spp)}</span></div>
              <div className="flex justify-between"><span className="text-emerald-600">● после СПП</span><span>{fmtMoney(data.realization?.after_spp)}</span></div>
              <div className="flex justify-between text-slate-400"><span>к перечислению от WB</span><span>{fmtMoney(data.payout_from_wb)}</span></div>
            </div>
          </div>
          <div className={card}>
            <div className={label}>Услуги</div>
            <div className="mt-1 text-[24px] font-black tracking-tight text-slate-950">{fmtMoney(data.services?.total)}</div>
            <div className="mt-3 space-y-1 text-[12px] font-bold">
              <div className="flex justify-between"><span className="text-amber-500">● Комиссия</span><span>{fmtMoney(data.services?.commission)}</span></div>
              <div className="flex justify-between"><span className="text-sky-500">● Логистика</span><span>{fmtMoney(data.services?.logistics)}</span></div>
              <div className="flex justify-between"><span className="text-violet-500">● Реклама</span><span>{fmtMoney(data.services?.adv)}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">● Хранение</span><span>{fmtMoney(data.services?.storage)}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">● Остальные</span><span>{fmtMoney(data.services?.other)}</span></div>
            </div>
          </div>
          <div className={card}>
            <div className={label}>Налоги и затраты</div>
            <div className="mt-1 text-[24px] font-black tracking-tight text-slate-950">{fmtMoney((data.taxes_and_costs?.tax || 0) + (data.taxes_and_costs?.vat || 0) + (data.taxes_and_costs?.cogs || 0))}</div>
            <div className="mt-3 space-y-1 text-[12px] font-bold">
              <div className="flex justify-between"><span className="text-orange-500">● Налог ({data.mode_label}, {fmtNum(data.rate, 1)}%)</span><span>{fmtMoney(data.taxes_and_costs?.tax)}</span></div>
              <div className="flex justify-between"><span className="text-rose-400">● НДС</span><span>{fmtMoney(data.taxes_and_costs?.vat)}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">● НДС к возмещению</span><span>-{fmtMoney(data.taxes_and_costs?.vat_refund)}</span></div>
              <div className="flex justify-between"><span className="text-rose-500">● Себестоимость продаж</span><span>{fmtMoney(data.taxes_and_costs?.cogs)}</span></div>
            </div>
            <div className="mt-2 text-[11px] font-semibold text-slate-400">налоговая база: {fmtMoney(data.taxes_and_costs?.tax_base)} ₽</div>
          </div>
          <div className={card}>
            <div className={label}>Операционная прибыль</div>
            <div className={`mt-1 text-[26px] font-black tracking-tight ${Number(data.operating_profit) < 0 ? "text-rose-600" : "text-emerald-600"}`}>
              {fmtMoney(data.operating_profit)} ₽
            </div>
            <div className="mt-3 text-[12px] font-semibold leading-5 text-slate-400">
              = реализация после СПП − услуги WB − себестоимость − налог − НДС (+возмещение).
              {Number(data.taxes_and_costs?.cogs_missing_barcodes) > 0 &&
                ` ⚠ Нет себестоимости для ${data.taxes_and_costs.cogs_missing_barcodes} баркодов.`}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
