import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // `VITE_BASE_PATH` is unset for the default (root-path) build local
  // Docker/desktop use -- only the STFC Cloud shared deployment sets it
  // (`.env.stfc-cloud`, built via `npm run build:stfc-cloud`), since it's
  // mounted at `/agent/` under a shared domain, not served from `/` (design
  // doc §19.3). Loaded via `loadEnv` rather than `import.meta.env` because
  // this runs at config-evaluation time, before Vite's own env injection.
  const env = loadEnv(mode, process.cwd(), '')
  return {
    base: env.VITE_BASE_PATH || '/',
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
  }
})
