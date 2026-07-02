export type TabType = 'analytics' | 'wb' | 'zoom' | 'team' | 'dialogs' | 'agents' | 'knowledge' | 'monitoring' | 'settings';

export interface Chat {
  id: string;
  userName: string;
  userRole: string;
  avatarInitials: string;
  avatarColor: string;
  time: string;
  lastMessage: string;
  tag: 'ops' | 'faq' | 'ошибка';
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
}
