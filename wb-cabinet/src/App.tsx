import { useEffect, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { DashboardContent, PAGE_TITLES } from "./components/DashboardContent";
import { usePage } from "./lib/router";
import { cn } from "./lib/utils";

export default function App() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [page, navigate] = usePage();

  useEffect(() => {
    document.title = `${PAGE_TITLES[page]} — WB-кабинет`;
  }, [page]);

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
          activePage={page === 'settings' ? 'settings' : 'wb'}
          onPageChange={(sidebarItem) => {
            // This standalone page IS the WB cabinet. Any other nav item returns to the main app.
            if (sidebarItem !== 'wb' && sidebarItem !== 'settings') {
              window.location.href = '/main';
              return;
            }
            navigate(sidebarItem === 'settings' ? 'settings' : 'dashboard');
            setIsSidebarOpen(false);
          }}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <TopBar
          onMenuClick={() => setIsSidebarOpen(true)}
          title={PAGE_TITLES[page]}
        />
        <DashboardContent page={page} onNavigate={navigate} />
      </div>
    </div>
  );
}
