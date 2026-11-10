// End-to-end tests of the web (shared-deployment) build against a staging
// agent set up like the STFC VM's. `uv run poe e2e-web` builds that frontend,
// starts the agent and goldilocks-core, and runs these; see app/e2e/README.md.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.e2e.ts",
  // One agent, one core, one local model: run serially. A chat turn on a
  // laptop's Ollama can take minutes.
  workers: 1,
  fullyParallel: false,
  timeout: 10 * 60_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  outputDir: "../test-results/e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8096",
    viewport: { width: 1500, height: 950 },
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
