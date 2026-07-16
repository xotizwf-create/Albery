// Data layer for the WB-cabinet page. All reads hit /api/wb-cab/* (which serve ONLY our
// Postgres, filled by the wb_sync tick engine). Same-origin cookie carries the admin session.

export async function api<T = any>(path: string): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function apiPost<T = any>(path: string, body: any): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

export function qs(params: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  const s = p.toString();
  return s ? "?" + s : "";
}

/** Money, integer, ru-RU grouping. Empty/NaN -> "—". */
export function money(v: any): string {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || isNaN(n)) return "—";
  return Math.round(n).toLocaleString("ru-RU");
}

/** Number with optional decimals. Empty/NaN -> "—". */
export function num(v: any, digits = 0): string {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || isNaN(n)) return "—";
  return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function pct(part: any, whole: any): string {
  const p = Number(part), w = Number(whole);
  if (!isFinite(p) || !isFinite(w) || w === 0) return "0%";
  return Math.round((p / w) * 100) + "%";
}
