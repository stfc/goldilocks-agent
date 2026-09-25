import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      // goldilocks-workbench's HttpCoreClient calls these paths relative to
      // its own origin (see goldilocks-core/web's vite.config.ts, which
      // proxies the same set) -- forward them to a locally running core
      // server (`uv run` with the `http` extra, port 8000).
      '/capabilities': 'http://127.0.0.1:8000',
      '/explain': 'http://127.0.0.1:8000',
      '/magnetic-orderings': 'http://127.0.0.1:8000',
      '/run': 'http://127.0.0.1:8000',
      '/inspect': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/ready': 'http://127.0.0.1:8000',
      '/openapi.json': 'http://127.0.0.1:8000',
    },
  },
})
