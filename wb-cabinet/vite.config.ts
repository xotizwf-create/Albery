import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Standalone WB-cabinet page, served by Flask at /Analytics. All assets under /analytics/.
export default defineConfig({
  base: '/analytics/',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
});
