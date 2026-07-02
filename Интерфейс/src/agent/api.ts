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
  id: d.dialog_id,
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
  channels: a.channels as AgentConfig["channels"],
  toolsCount: 0,
  skillsCount: 0,
  usersCount: a.users_count,
  usersInfo: a.users_preview,
  stats: { movesToday: a.turns_today, avgSpeed: a.avg_speed },
  iconBg: a.icon_bg,
  iconType: ICON_TYPES[a.icon] || "box",
});

export async function fetchAgentDialogs(params: { channel: string; q?: string }): Promise<{ chats: Chat[]; note?: string }> {
  const search = new URLSearchParams({ channel: params.channel.toLowerCase() });
  if (params.q) search.set("q", params.q);
  const data = await fetchJsonSafe(`/api/agent-center/dialogs?${search}`, undefined, 30000);
  return { chats: ((data.dialogs || []) as RawDialog[]).map(toChat), note: data.note };
}

export async function fetchDialogTurns(dialogId: string): Promise<DialogTurn[]> {
  const search = new URLSearchParams({ dialog_id: dialogId });
  const data = await fetchJsonSafe(`/api/agent-center/dialog-messages?${search}`, undefined, 30000);
  return (data.turns || []) as DialogTurn[];
}

export async function fetchAgents(): Promise<AgentConfig[]> {
  const data = await fetchJsonSafe("/api/agent-center/agents", undefined, 30000);
  return ((data.agents || []) as RawAgent[]).map(toAgent);
}

export async function fetchKnowledge(): Promise<KnowledgeItem[]> {
  const data = await fetchJsonSafe("/api/agent-center/knowledge", undefined, 30000);
  const items = (data.items || []) as Array<{
    id: string;
    title: string;
    parent: string;
    description: string;
    has_content: boolean;
    updated: string;
  }>;
  return items
    .filter((i) => i.has_content)
    .map((i) => ({
      id: i.id,
      title: i.parent ? `${i.parent} / ${i.title}` : i.title,
      description: i.description,
      type: "Инструкция" as const,
      updatedAt: i.updated,
    }));
}
