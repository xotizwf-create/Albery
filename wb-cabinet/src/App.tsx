import { useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { DashboardContent } from "./components/DashboardContent";
import { SettingsContent } from "./components/SettingsContent";
import { cn } from "./lib/utils";

export default function App() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [activePage, setActivePage] = useState("wb");

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-900 overflow-hidden">
      
      {/* Mobile sidebar overlay */}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-40 lg:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}
      
      {/* Sidebar container */}
      <div className={cn(
        "fixed inset-y-0 left-0 z-50 transform transition-transform duration-300 ease-in-out lg:relative lg:translate-x-0 w-64 shrink-0 shadow-2xl lg:shadow-none",
        isSidebarOpen ? "translate-x-0" : "-translate-x-full"
      )}>
        <Sidebar
          onClose={() => setIsSidebarOpen(false)}
          activePage={activePage}
          onPageChange={(page) => {
            // This standalone page IS the WB cabinet. Any other nav item returns to the main app.
            if (page !== 'wb' && page !== 'settings') {
              window.location.href = '/main';
              return;
            }
            setActivePage(page);
            setIsSidebarOpen(false);
          }}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <TopBar 
          onMenuClick={() => setIsSidebarOpen(true)} 
          title={activePage === 'settings' ? 'Настройки' : 'WB-кабинет'}
        />
        {activePage === 'wb' && <DashboardContent />}
        {activePage === 'settings' && <SettingsContent />}
        {activePage !== 'wb' && activePage !== 'settings' && (
          <div className="flex-1 flex items-center justify-center p-8">
            <div className="text-center">
              <h2 className="text-xl font-bold text-slate-900 mb-2">Раздел в разработке</h2>
              <p className="text-slate-500">Этот раздел будет доступен в ближайшем обновлении.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
