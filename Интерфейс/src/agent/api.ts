// Data layer for the "Центр Агента" section: typed fetchers over the read-only
// /api/agent-center/* endpoints plus mapping into the view models the screens
// render. Views stay purely presentational; everything network lives here.

import { fetchJsonSafe } from "../api/client";
import { AgentConfig, Chat, KnowledgeItem } from "./types";

export interface DialogTurn {
  id: number;
  date: string;
  time: string;
  question: string;
  answer: string;
  status: string;
  error: string;
  latency_ms: number | null;
  tier: string;
  session_name: string;
}

interface RawDialog {
  dialog_id: string;
  bitrix_user_id: number | null;
  user_name: string;
  user_position: string;
  tier: string;
  agent_slug: string | null;
  last_message: string;
  last_status: string;
  turns: number;
  errors: number;
  time: string;
}

interface RawAgent {
  id: string;
  name: string;
  kind: string;
  icon: string;
  icon_bg: string;
  is_system: boolean;
  is_active: boolean;
  channels: string[];
  users_count: number;
  users_preview: string;
  turns_today: number;
  turns_7d: number;
  errors_7d: number;
  avg_speed: string;
  last_at: string;
}

const AVATAR_PALETTE = [
  "bg-blue-500",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-purple-500",
  "bg-pink-500",
  "bg-sky-500",
  "bg-orange-500",
  "bg-slate-400",
];

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join("") || "??";

const avatarColor = (key: string) => {
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  return AVATAR_PALETTE[Math.abs(hash) % AVATAR_PALETTE.length];
};

const toChat = (d: RawDialog): Chat => ({
  id: `${d.agent_slug || "main"}:${d.dialog_id}`,
  dialogId: d.dialog_id,
  agentId: d.agent_slug || "main",
  userName: d.user_name,
  userRole: d.user_position || `диалог ${d.dialog_id}`,
  avatarInitials: initials(d.user_name),
  avatarColor: avatarColor(d.dialog_id),
  time: d.time,
  lastMessage: d.last_message || "…",
  tag: d.errors > 0 ? "ошибка" : (d.tier as Chat["tag"]),
  channel: "Bitrix",
});

const ICON_TYPES: Record<string, AgentConfig["iconType"]> = {
  zap: "zap",
  book: "book",
  crown: "crown",
};

const toAgent = (a: RawAgent): AgentConfig => ({
  id: a.id,
  name: a.name,
  type: a.kind,
  isActive: a.is_active,
  isSystem: !!a.is_system,
  channels: a.channels as AgentConfig["channels"],
  toolsCount: 0,
  skillsCount: 0,
  usersCount: a.users_count,
  usersInfo: a.users_preview,
  stats: { movesToday: a.turns_today, avgSpeed: a.avg_speed },
  iconBg: a.icon_bg,
  iconType: ICON_TYPES[a.icon] || "box",
});

// --- Subagents ---

export type AgentLevel = "faq" | "ops" | "developer";

export const LEVEL_LABELS: Record<AgentLevel, string> = {
  faq: "База знаний",
  ops: "Все функции",
  developer: "Разработчик",
};

export interface AgentDetail {
  slug: string;
  name: string;
  position: string;
  role_prompt: string;
  tier: AgentLevel;
  is_main?: boolean;
  is_active: boolean;
  bitrix_bot_id: number | null;
  members: Array<{ id: number; name: string }>;
  instructions: Array<{
    id: string;
    name: string;
    content: string;
    source: "owner" | "self";
    created_by?: string;
    updated_by?: string;
    origin_dialog?: string;
    created?: string;
    updated: string;
  }>;
}

export async function createAgent(body: {
  name: string;
  tier: AgentLevel;
  position?: string;
  role_prompt: string;
  members: number[];
}): Promise<{ slug: string; bitrix_bot_id: number | null; warnings: string[] }> {
  return await fetchJsonSafe(
    "/api/agent-center/agents",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    60000,
  );
}

export async function fetchAgentDetail(slug: string): Promise<AgentDetail> {
  return (await fetchJsonSafe(`/api/agent-center/agents/${slug}`, undefined, 30000)) as AgentDetail;
}

export async function updateAgent(
  slug: string,
  body: Partial<{ name: string; position: string; role_prompt: string; tier: string; is_active: boolean; members: number[] }>,
): Promise<void> {
  await fetchJsonSafe(
    `/api/agent-center/agents/${slug}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    30000,
  );
}

export async function deleteAgent(slug: string): Promise<void> {
  await fetchJsonSafe(`/api/agent-center/agents/${slug}`, { method: "DELETE" }, 60000);
}

export async function registerAgentBot(slug: string): Promise<{ bitrix_bot_id: number | null; warnings: string[] }> {
  return await fetchJsonSafe(`/api/agent-center/agents/${slug}/register-bot`, { method: "POST" }, 60000);
}

export async function addAgentInstruction(slug: string, name: string, content: string): Promise<void> {
  await fetchJsonSafe(
    `/api/agent-center/agents/${slug}/instructions`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, content }) },
    30000,
  );
}

export async function deleteAgentInstruction(slug: string, instId: string): Promise<void> {
  await fetchJsonSafe(`/api/agent-center/agents/${slug}/instructions/${instId}`, { method: "DELETE" }, 30000);
}

// Promote a personal instruction into the shared library (an optional instruction that
// can then be connected to any agent). The personal copy stays with the agent.
export async function promoteAgentInstruction(slug: string, instId: string): Promise<{ name: string; path: string }> {
  return (await fetchJsonSafe(
    `/api/agent-center/agents/${slug}/instructions/${instId}/promote`,
    { method: "POST" },
    30000,
  )) as { name: string; path: string };
}

// --- Per-agent capability config (tools / library instructions / skills) ---
// Every field maps to real backend enforcement: a disabled tool is not served by the
// agent's connector; an unselected instruction/skill is never injected into its turn.

export interface AgentConfigTool {
  name: string;
  description: string;
  tiers: string[];
  class: "faq" | "ops" | "admin"; // admin = owner-only/dangerous
  core: boolean;
  fixed: boolean; // mandatory baseline — always on, cannot be disabled
  enabled: boolean;
  allowed: boolean; // false = this level may not hold it (admin tool on a non-developer)
}

export interface AgentConfigKnowledge {
  id: string;
  title: string;
  parent: string;
  description: string;
  custom?: boolean;
  scope?: "universal" | "optional"; // instructions: universal = every agent; optional = per-agent
  kind?: string; // skills: "shared" | "hermes_base"
  selected: boolean;
}

export interface AgentCapabilityConfig {
  slug: string;
  tier: AgentLevel;
  tools_customized: boolean;
  tools: AgentConfigTool[];
  tools_total: number;
  instructions: AgentConfigKnowledge[];
  skills: AgentConfigKnowledge[];
}

export async function fetchAgentConfig(slug: string): Promise<AgentCapabilityConfig> {
  return (await fetchJsonSafe(`/api/agent-center/agents/${slug}/config`, undefined, 30000)) as AgentCapabilityConfig;
}

export async function saveAgentConfig(
  slug: string,
  body: { tools: string[]; instructions: string[]; skills: string[] },
): Promise<void> {
  await fetchJsonSafe(
    `/api/agent-center/agents/${slug}/config`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    30000,
  );
}

// Flip a library instruction between universal (all agents) and optional (per-agent).
// Library-level change: edits the instruction's frontmatter in the GitHub registry.
export async function setInstructionScope(path: string, scope: "universal" | "optional"): Promise<void> {
  await fetchJsonSafe(
    `/api/agent-center/knowledge/instruction-scope`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, scope }) },
    30000,
  );
}

export async function fetchAgentDialogs(params: { channel: string; q?: string; agent?: string }): Promise<{
  chats: Chat[];
  note?: string;
}> {
  const search = new URLSearchParams({ channel: params.channel.toLowerCase() });
  if (params.q) search.set("q", params.q);
  // Per-agent isolation: "all" = every bot; otherwise only the selected bot's dialogs.
  if (params.agent && params.agent !== "all") search.set("agent", params.agent);
  const data = await fetchJsonSafe(`/api/agent-center/dialogs?${search}`, undefined, 30000);
  return { chats: ((data.dialogs || []) as RawDialog[]).map(toChat), note: data.note };
}

export async function fetchDialogTurns(dialogId: string, agent?: string): Promise<DialogTurn[]> {
  const search = new URLSearchParams({ dialog_id: dialogId });
  // Scope the thread to the bot — a dialog_id is shared across bots, so without this the
  // thread would mix in other bots' turns with the same user.
  if (agent && agent !== "all") search.set("agent", agent);
  const data = await fetchJsonSafe(`/api/agent-center/dialog-messages?${search}`, undefined, 30000);
  return (data.turns || []) as DialogTurn[];
}

export async function fetchAgents(): Promise<AgentConfig[]> {
  const data = await fetchJsonSafe("/api/agent-center/agents", undefined, 30000);
  return ((data.agents || []) as RawAgent[]).map(toAgent);
}

// --- MCP tools (the real registry the agent runs on) ---

export interface McpTool {
  name: string;
  description: string;
  tiers: string[];
  core: boolean;
}

export async function fetchMcpTools(): Promise<McpTool[]> {
  const data = await fetchJsonSafe("/api/agent-center/tools", undefined, 30000);
  return (data.tools || []) as McpTool[];
}

// --- Monitoring ---

export interface MonitoringData {
  status: {
    uptime: string;
    last_turn: string;
    slots_busy: number | null;
    slots_total: number | null;
    version: string;
  };
  cards: Array<{ label: string; value: string; sub: string; tone: "good" | "bad" | "muted" }>;
  chart: Array<{ time: string; speed: number; error: boolean }>;
  health: Array<{ label: string; status: string; type: "ok" | "warn" }>;
  events: Array<{ time: string; type: string; text: string }>;
  problems: string[];
}

export async function fetchMonitoring(chartDays = 1): Promise<MonitoringData> {
  return (await fetchJsonSafe(
    `/api/agent-center/monitoring?chart_days=${chartDays}`,
    undefined,
    30000,
  )) as MonitoringData;
}

// --- Usage accounting ---

export interface UsageRow {
  bitrix_user_id: number | null;
  name: string;
  position: string;
  turns: number;
  time_ms: number;
  time_label: string;
  agent_time_label: string;
  errors: number;
  tokens_est: number;
  cache_tokens: number;
  matched: number;
  last_at: string;
}

export interface UsageData {
  period: string;
  rows: UsageRow[];
  totals: {
    turns: number;
    time_ms: number;
    time_label: string;
    agent_time_label: string;
    tokens_est: number;
    cache_tokens: number;
    users: number;
    matched_turns: number;
    coverage_pct: number;
  };
}

export async function fetchUsage(period: string): Promise<UsageData> {
  const search = new URLSearchParams({ period });
  return (await fetchJsonSafe(`/api/agent-center/usage?${search}`, undefined, 30000)) as UsageData;
}

// --- Team access (existing /api/agent-access CRUD, shared with the Настройки tab) ---

export type AccessTier = "admin" | "ops" | "faq";

export const TIER_LABELS: Record<AccessTier, string> = {
  admin: "Полный доступ",
  ops: "Все функции",
  faq: "Доступ к FAQ",
};

export interface AccessMember {
  bitrix_user_id: number;
  tier: AccessTier;
  display_name: string;
}

export interface BitrixUser {
  id: number;
  name: string;
  email: string;
  position: string;
}

export async function fetchAccessMembers(): Promise<AccessMember[]> {
  const data = await fetchJsonSafe("/api/agent-access", undefined, 30000);
  return ((data.rows || []) as Array<{ bitrix_user_id: number; tier: string; display_name: string | null }>)
    .filter((r) => r.tier === "admin" || r.tier === "ops" || r.tier === "faq")
    .map((r) => ({
      bitrix_user_id: r.bitrix_user_id,
      tier: r.tier as AccessTier,
      display_name: r.display_name || `#${r.bitrix_user_id}`,
    }));
}

export async function fetchBitrixUsers(): Promise<BitrixUser[]> {
  const data = await fetchJsonSafe("/api/agent-access/bitrix-users", undefined, 60000);
  return (data.users || []) as BitrixUser[];
}

// Bot semantics: no row = default «faq»; an explicit "none" row is the only real deny.
export async function upsertAccess(userId: number, tier: AccessTier | "none", displayName?: string): Promise<void> {
  await fetchJsonSafe(
    "/api/agent-access",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bitrix_user_id: userId, tier, display_name: displayName }),
    },
    30000,
  );
}

export async function fetchKnowledge(): Promise<KnowledgeItem[]> {
  const data = await fetchJsonSafe("/api/agent-center/knowledge", undefined, 30000);
  const items = (data.items || []) as Array<{
    id: string;
    title: string;
    parent: string;
    description: string;
    type: string;
    custom: boolean;
    has_content: boolean;
    updated: string;
  }>;
  return items
    .filter((i) => i.has_content)
    .map((i) => ({
      id: i.id,
      title: i.parent ? `${i.parent} / ${i.title}` : i.title,
      description: i.description,
      type: (i.type === "Скилл" ? "Скилл" : "Инструкция") as KnowledgeItem["type"],
      updatedAt: i.updated,
      custom: i.custom,
    }));
}
