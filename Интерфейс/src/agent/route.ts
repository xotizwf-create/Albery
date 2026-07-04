// Deep-linking for the Центр Агента pages: each agent and each dialog gets its own URL
// (e.g. /agent/main, /agent-dialogs/main/22) so a refresh or a shared link restores the
// exact selection instead of resetting. App.tsx routes the tab by the path's base; the
// views own the sub-segments after it and keep them in sync with their selection.

// Segments after a base route: "/agent-dialogs/main/22" under "/agent-dialogs" -> ["main","22"].
export function agentSubSegments(base: string): string[] {
  const p = window.location.pathname;
  if (p === base) return [];
  if (p.startsWith(base + "/")) {
    return p
      .slice(base.length + 1)
      .split("/")
      .filter(Boolean)
      .map((s) => decodeURIComponent(s));
  }
  return [];
}

// Write the base + segments into the URL. `replace` (default) updates without adding a
// history entry — enough for "survives refresh / shareable" without spamming back/forward.
export function setAgentPath(base: string, segments: Array<string | null | undefined>, replace = true): void {
  const clean = segments.filter((s): s is string => !!s).map((s) => encodeURIComponent(s));
  const path = base + (clean.length ? "/" + clean.join("/") : "");
  if (window.location.pathname === path) return;
  if (replace) window.history.replaceState({}, "", path);
  else window.history.pushState({}, "", path);
}
