import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Development-only proxy: the browser talks to the Vite dev server (same
// origin) and Vite forwards API calls to the FastAPI backend, so the backend
// needs no CORS configuration.
//
// `/health` is proxied as well because the backend serves its liveness check
// there (outside `/api`); the connection indicator in the header uses it.
const BACKEND_URL = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: BACKEND_URL, changeOrigin: true },
      '/health': { target: BACKEND_URL, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    unstubGlobals: true,
    css: false,
  },
})
