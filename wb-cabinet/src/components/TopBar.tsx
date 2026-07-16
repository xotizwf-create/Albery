import { Bell, Menu } from "lucide-react";

interface TopBarProps {
  onMenuClick: () => void;
  title: string;
}

export function TopBar({ onMenuClick, title }: TopBarProps) {
  return (
    <header className="h-16 border-b border-slate-200/60 bg-white flex items-center justify-between px-4 lg:px-6 shrink-0">
      <div className="flex items-center gap-3">
        <button 
          onClick={onMenuClick}
          className="p-2 -ml-2 text-slate-400 hover:text-slate-600 lg:hidden"
        >
          <Menu className="w-6 h-6" />
        </button>
        <div className="hidden sm:flex items-center gap-2 text-sm">
          <div className="flex items-center gap-2 bg-slate-50 px-3 py-1.5 rounded-full border border-slate-200/50 text-brand-600 font-medium">
            Alberi Workspace
            <button className="text-slate-400 hover:text-slate-600">×</button>
          </div>
          <span className="text-slate-400">/</span>
          <span className="text-slate-600 font-medium">{title}</span>
        </div>
      </div>

      <div className="flex items-center gap-3 lg:gap-4">
        <button className="relative p-2 text-slate-400 hover:text-slate-600 transition-colors">
          <Bell className="w-5 h-5" />
          <span className="absolute top-2 right-2.5 w-2 h-2 bg-red-500 border-2 border-white rounded-full"></span>
        </button>
        <div className="w-8 h-8 rounded-full border border-slate-200 flex items-center justify-center bg-slate-50 text-slate-600 font-medium text-sm">
          Е
        </div>
      </div>
    </header>
  );
}
