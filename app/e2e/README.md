# End-to-end tests of the web build

Playwright tests of what users of workbench.goldilocks.ac.uk get, run against a
local staging agent set up like the STFC VM's. Run them before each VM rollout.

```bash
uv run poe e2e-web                 # everything
uv run poe e2e-web -- -g ranking   # tests whose title matches
E2E_OFFLINE=1 uv run poe e2e-web   # skip the test that needs Materials Cloud
uv run poe e2e-web --serve         # only start the staging agent (http://127.0.0.1:8096), to try it by hand; Ctrl-C stops it
```

Against a staging agent that is already running (`--serve` in another
terminal), run the tests directly:
`cd app && E2E_BASE_URL=http://127.0.0.1:8096 npx playwright test -c e2e/playwright.config.ts`.

`scripts/e2e_web.py` does three things:

1. **Builds** the shared-deployment frontend (`npm run build:stfc-cloud`).
2. **Starts the agent** on port 8096 with:
   - `GOLDILOCKS_AGENT_SHARED_DEPLOYMENT=1`;
   - the chat model pinned to local Ollama's qwen3.8 (`E2E_MODEL` overrides it);
   - goldilocks-core auto-started from PyPI at `core_pin.json`'s version, on port 8000. The VM runs the same release from a checkout.
3. **Runs these tests** against it, then stops the agent and its core.

HOME is a temporary directory, so:

- no API keys or saved preferences leak in;
- test chats don't land in your own `~/.goldilocks`;
- uv's cache and Python installs and core's assets stay where they are.

**One-time setup:**

- `cd app && npm install && npx playwright install chromium`;
- Ollama running with the model.

**Before a run:** ports 8096 and 8000 must be free. A core already on 8000 would be reused, and might not be the pinned release.

| File | Model? | What it checks |
|---|---|---|
| `api.e2e.ts` | no | the pinned core is the one behind the proxy; single-user routes and MLIP Playground are refused; ranking is a job (submit, cancel), and the old synchronous call is refused |
| `tools.e2e.ts` | no | All Tools: the web build's tools, the local version's (MLIP Playground, AiiDAlab-QE), and Beyond DFT |
| `experience.e2e.ts` | request only | the onboarding level travels with each chat request; `/api/preferences` is never called |
| `ranking.e2e.ts` | no | Run mMACE in the embedded Workbench shows the job queued or running, then a recommended ordering |
| `chat-dft.e2e.ts` | yes | attach → open → review → generate: core's values in the review card, the bundle under Generated Files and its download, all kept on reload |
| `database.e2e.ts` | yes, network | `get_structure` → `dft_open_structure` by the fetched file's name |
| `language.e2e.ts` | yes | an English question gets English, a Chinese one Chinese |

The tests check what the tools leave behind — cards, requests, downloads — not
what the model says (AGENTS.md). The one exception is the reply's language.
Tests that drive the model take minutes on a laptop's Ollama.
