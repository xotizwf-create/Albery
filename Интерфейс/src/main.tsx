import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import './index.css';

async function bootstrap() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';
  const RootComponent =
    pathname === '/agent-funnels'
      ? (await import('./funnel-workspace/FunnelWorkspace.tsx')).FunnelWorkspace
      : (await import('./App.tsx')).default;

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <RootComponent />
    </StrictMode>,
  );
}

void bootstrap();
