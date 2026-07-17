// Tiny history-API router for the standalone WB-cabinet SPA.
// Flask serves index.html for any unknown /analytics/<sub-path> (SPA fallback),
// so every page below is deep-linkable and survives a refresh.

import { useCallback, useEffect, useState } from 'react';

export type PageId = 'dashboard' | 'rnp' | 'pnl' | 'cashflow' | 'articles' | 'tax' | 'settings';

const BASE = '/analytics';

const SLUG_TO_PAGE: Record<string, PageId> = {
  '': 'dashboard',
  rnp: 'rnp',
  pnl: 'pnl',
  cashflow: 'cashflow',
  articles: 'articles',
  tax: 'tax',
  settings: 'settings',
};

function parsePage(pathname: string): PageId {
  const rest = pathname
    .replace(/^\/analytics\/?/i, '')
    .replace(/\/+$/, '')
    .toLowerCase();
  return SLUG_TO_PAGE[rest] ?? 'dashboard';
}

export function pagePath(page: PageId): string {
  return page === 'dashboard' ? BASE : `${BASE}/${page}`;
}

export function usePage(): [PageId, (page: PageId) => void] {
  const [page, setPage] = useState<PageId>(() => parsePage(window.location.pathname));

  useEffect(() => {
    const onPopState = () => setPage(parsePage(window.location.pathname));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const navigate = useCallback((next: PageId) => {
    const path = pagePath(next);
    if (window.location.pathname !== path) window.history.pushState({}, '', path);
    setPage(next);
  }, []);

  return [page, navigate];
}
