// Canonical HTTP client for the admin UI.
//
// Every call to the backend should go through `fetchJsonSafe` so we get one
// consistent place for: a request timeout (so a hung backend never freezes the
// UI), tolerant JSON parsing (an empty or non-JSON body becomes a clear error
// instead of a raw `SyntaxError`), and a uniform `!response.ok` -> thrown error
// contract. The thrown `Error.message` prefers the backend's `error` field and
// falls back to a status-based message.
//
// NOTE: a number of legacy call sites in App.tsx still use bare `fetch()`.
// Migrating them here is safe for read (GET) endpoints, but report-generation
// POSTs can legitimately run longer than the default timeout, so each of those
// must be moved with an explicit `timeoutMs` rather than blanket-converted.

export async function fetchJsonSafe(
  url: string,
  options?: RequestInit,
  timeoutMs = 180000,
): Promise<any> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...(options || {}), signal: controller.signal });
    const rawText = await response.text();
    let payload: any = {};
    try {
      payload = rawText ? JSON.parse(rawText) : {};
    } catch {
      throw new Error(`Сервер вернул не-JSON ответ (${response.status}). Проверьте backend-логи.`);
    }
    if (!response.ok) throw new Error(payload?.error || `Ошибка запроса (${response.status})`);
    return payload;
  } finally {
    window.clearTimeout(timer);
  }
}
