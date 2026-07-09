export type TabType = 'analytics' | 'wb' | 'zoom' | 'team' | 'dialogs' | 'agents' | 'knowledge' | 'monitoring' | 'settings';

export interface Chat {
  id: string; // composite key `${agentId}:${dialogId}` — unique per bot+user (a dialog_id is shared across bots)
  dialogId: string; // raw Bitrix dialog_id (the user, or "task-<id>" for in-task mentions)
  taskId?: number; // set only for in-task mention threads (dialog_id "task-<id>")
  agentId: string; // which bot this dialog belongs to ('main' or a subagent slug)
  userName: string;
  userRole: string;
  avatarInitials: string;
  avatarColor: string;
  time: string;
  lastMessage: string;
  tag: 'ops' | 'faq' | 'admin' | 'ошибка';
  channel: 'Bitrix' | 'Telegram';
}

export interface Message {
  id: string;
  sender: 'user' | 'agent';
  text?: string;
  time?: string;
  card?: {
    title: string;
    status?: 'success' | 'pending' | 'error';
    lines: string[];
    meta: string;
  };
}

export interface AgentConfig {
  id: string;
  name: string;
  type: string;
  isActive: boolean;
  isSystem: boolean;
  channels: ('Bitrix' | 'Telegram')[];
  toolsCount: number;
  skillsCount: number;
  usersCount: number;
  usersInfo?: string;
  stats: {
    movesToday: number;
    avgSpeed: string;
  };
  iconBg: string;
  iconType: 'zap' | 'book' | 'crown' | 'box';
}

export interface KnowledgeItem {
  id: string;
  title: string;
  description: string;
  type: 'Инструкция' | 'Скилл' | 'Регламент';
  updatedAt: string;
  isLocked?: boolean;
  custom?: boolean;
}
