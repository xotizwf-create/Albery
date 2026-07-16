import { 
  LayoutDashboard, 
  Store, 
  Video, 
  Building2, 
  Users, 
  MessageSquare, 
  HeadphonesIcon, 
  BookOpen, 
  Activity, 
  Settings,
  CircleUser,
  X
} from "lucide-react";
import { cn } from "../lib/utils";

const NAV_GROUPS = [
  {
    title: "Компания",
    items: [
      { id: "analytics", label: "Сводная аналитика", icon: LayoutDashboard },
      { id: "wb", label: "WB-кабинет", icon: Store },
      { id: "zoom", label: "Зум-разговоры", icon: Video },
      { id: "about", label: "О компании", icon: Building2 },
      { id: "team", label: "Команда", icon: Users },
    ],
  },
  {
    title: "Центр агента",
    items: [
      { id: "dialogs", label: "Диалоги", icon: MessageSquare },
      { id: "agents", label: "Агенты", icon: HeadphonesIcon },
      { id: "knowledge", label: "База знаний", icon: BookOpen },
      { id: "monitoring", label: "Мониторинг", icon: Activity },
      { id: "usage", label: "Использование", icon: LayoutDashboard },
    ],
  },
  {
    title: "Прочее",
    items: [
      { id: "settings", label: "Настройки", icon: Settings },
    ],
  },
];

interface SidebarProps {
  onClose?: () => void;
  activePage: string;
  onPageChange: (pageId: string) => void;
}

export function Sidebar({ onClose, activePage, onPageChange }: SidebarProps) {
  return (
    <aside className="w-full h-full border-r border-slate-200/60 bg-white flex flex-col overflow-y-auto">
      {/* Logo */}
      <div className="p-6 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-brand-500 rounded-lg flex items-center justify-center text-white font-bold text-sm shadow-md shadow-brand-500/20">
            A
          </div>
          <span className="font-bold text-slate-900 text-lg tracking-tight">Alberi</span>
        </div>
        {onClose && (
          <button onClick={onClose} className="p-2 text-slate-400 hover:text-slate-600 lg:hidden">
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Nav Groups */}
      <div className="flex-1 px-4 pb-6 space-y-8">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-3 px-3">
              {group.title}
            </div>
            <nav className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = activePage === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => onPageChange(item.id)}
                    className={cn(
                      "w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium transition-colors",
                      isActive
                        ? "bg-brand-50 text-brand-600"
                        : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                    )}
                  >
                    <Icon className={cn("w-4 h-4", isActive ? "text-brand-500" : "text-slate-400")} />
                    {item.label}
                  </button>
                );
              })}
            </nav>
          </div>
        ))}
      </div>

      {/* Profile */}
      <div className="p-4 border-t border-slate-200/60 mt-auto">
        <button className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-colors">
          <CircleUser className="w-4 h-4 text-slate-400" />
          Мой профиль
        </button>
      </div>
    </aside>
  );
}
