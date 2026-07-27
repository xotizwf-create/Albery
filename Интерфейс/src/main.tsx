import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import './index.css';

async function bootstrap() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';
  // Ссылка на конкретное обращение — /agent-funnels/37. Точное сравнение открывало по
  // ней кабинет вместо рабочего окна, и человек попадал на пустой раздел.
  const isWorkspace =
    pathname === '/agent-funnels' || pathname.startsWith('/agent-funnels/');
  const RootComponent = isWorkspace
    ? (await import('./funnel-workspace/FunnelWorkspace.tsx')).FunnelWorkspace
    : (await import('./App.tsx')).default;

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <RootComponent />
    </StrictMode>,
  );
}

void bootstrap();
