import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import './index.css';
import {RootErrorBoundary, showBootstrapFailure} from './RootErrorBoundary.tsx';

async function bootstrap() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';
  // Ссылка на конкретное обращение — /agent-funnels/37. Точное сравнение открывало по
  // ней кабинет вместо рабочего окна, и человек попадал на пустой раздел.
  const isWorkspace =
    pathname === '/agent-funnels' || pathname.startsWith('/agent-funnels/');
  const RootComponent = isWorkspace
    ? (await import('./funnel-workspace/FunnelWorkspace.tsx')).FunnelWorkspace
    : (await import('./App.tsx')).default;

  const rootElement = document.getElementById('root');
  if (!rootElement) {
    throw new Error('Не найден корневой элемент интерфейса.');
  }

  createRoot(rootElement).render(
    <StrictMode>
      <RootErrorBoundary>
        <RootComponent />
      </RootErrorBoundary>
    </StrictMode>,
  );
}

void bootstrap().catch(showBootstrapFailure);
