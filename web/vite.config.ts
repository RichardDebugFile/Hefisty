import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Hefisty SPA — built to static assets served by the gateway at the root path.
export default defineConfig({
  // Assets are served from the gateway root, so use relative asset URLs.
  base: './',
  plugins: [react()],
  build: {
    // Default output dir is `dist`; kept explicit for clarity.
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    port: 5173,
    // During `npm run dev`, forward API + health calls to the local gateway.
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
});
