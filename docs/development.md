# Development

For working on goldilocks-agent itself. To run it, see
[Getting started](getting-started.md).

goldilocks-agent is the LLM orchestration layer on top of
[goldilocks-core](https://github.com/stfc/goldilocks-core): core decides DFT
parameters; this package decides when to ask the user, how to explain a
recommendation, and how to run it. The product design and a dated log of
what is built are in the repository's
[`docs/goldilocks-agent-design.md`](https://github.com/stfc/goldilocks-agent/blob/main/docs/goldilocks-agent-design.md)
and
[`docs/goldilocks-agent-implementation-plan.md`](https://github.com/stfc/goldilocks-agent/blob/main/docs/goldilocks-agent-implementation-plan.md)
(internal working documents, not on this site).
[`AGENTS.md`](https://github.com/stfc/goldilocks-agent/blob/main/AGENTS.md)
has the code style and rules.

## Layout

```
src/goldilocks_agent/   Python package: LangGraph orchestration, the HTTP/SSE server, Tools
app/                    React/Vite frontend: chat and the Tools panel. DFT Workbench's inline
                        panel and full-page view embed goldilocks-core's published Workbench UI
                        (design doc §13). Not called "web": goldilocks-core/web is a different product.
mlip-cli/               Its own project, just a `janus-core[mace]` pin: keeps torch/mace out of
                        goldilocks-agent's environment. MLIP Playground runs `janus` inside it.
deploy/stfc-cloud/      The website's deployment on STFC Cloud
tests/                  Backend tests; app/e2e/ has the web build's end-to-end tests
docs/                   This site, plus the internal design documents
```

## Running from source

Needs Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Node.js 18+ with npm,
and a model (a cloud API key or [Ollama](https://ollama.com), as in
[Getting started](getting-started.md#step-2-choose-a-language-model)).

```bash
git clone https://github.com/stfc/goldilocks-agent.git && cd goldilocks-agent
uv sync --group dev        # backend: Python dependencies into ./.venv
cd app && npm install      # frontend: JS dependencies into app/node_modules
```

Then, in two terminals:

```bash
uv run poe serve           # backend on http://127.0.0.1:8080
cd app && npm run dev      # frontend on http://localhost:5173, proxying /api to :8080
```

Open <http://localhost:5173>. The first run downloads the JARVIS DFT-3D
dataset (about 200 MB) in the background for Find in Databases' JARVIS
results; `uv run poe fetch-jarvis-cache` downloads it ahead of time.

API keys work as in [Configuration](configuration.md): environment variables
(`ANTHROPIC_API_KEY`, ...) or the Settings panel, which saves them to
`~/.config/goldilocks/config.toml` (mode `0600`); the environment variable
wins. `GOLDILOCKS_AGENT_MODEL` forces one litellm model for every chat.

### DFT Workbench

DFT Workbench needs a goldilocks-core HTTP backend. goldilocks-agent starts
one the first time something needs it (opening DFT Workbench, a chat DFT tool,
or reloading a page with a saved Workbench state), reusing one already
running on port 8000, and stops it when goldilocks-agent exits. Its output,
including the traceback behind any HTTP 500, goes to goldilocks-agent's log
prefixed `[goldilocks-core]` (`docker compose logs agent` in Docker). Two
ways to provide it:

- **From PyPI**: `export GOLDILOCKS_AGENT_CORE_AUTOSTART=1`. goldilocks-agent
  runs `uvx --from goldilocks-core[http]==<version> goldilocks serve http`,
  with `<version>` the release pinned in `src/goldilocks_agent/core_pin.json`,
  after `goldilocks assets install all`.
- **From your own checkout**, to try uncommitted core changes. It takes
  precedence over PyPI:

  ```bash
  cd /path/to/goldilocks-core && uv sync --extra http
  export GOLDILOCKS_CORE_PATH=/path/to/goldilocks-core
  ```

  In Docker, mount the checkout and set `GOLDILOCKS_CORE_PATH` as the
  commented lines in `docker-compose.yml` show. The checkout must speak the
  embedded Workbench UI's `vocabulary_version` (in `core_pin.json`; a
  checkout reports its own in `src/goldilocks_core/capabilities.py`), or
  goldilocks-agent reports both versions instead of loading DFT Workbench.

The magnetism ML tier (mMACE) needs no setup since core v0.3.0: `mace-torch`
is a core dependency and the assets install brings the model. Load a magnetic
structure (core's `src/goldilocks_core/examples/structures/Fe_bcc.cif`) and
the Analysis card's "is magnetic" reads "Goldilocks-ML prediction". Coming
from an older setup:

- unset `GOLDILOCKS_MACE_BACKBONE` if it points at the v1.0 checkpoint
  (`mace_matpes_pbe_baseline_run-3.model`), which core refuses;
- a checkout with the `CheukHinHoJerry/mace` fork or `sphericart` installed
  by hand gets its locked `mace-torch` back with `uv sync --extra http`;
- the first start after an upgrade fetches changed models and the PseudoDojo
  tables again, which can take many minutes.

goldilocks-core's `docs/mmace-setup.md` has the details. mMACE is unrelated
to MLIP Playground's MACE (a different package, purpose and checkpoint).

### The embedded Workbench and the core pin

The Workbench UI is a vendored npm tarball in `app/vendor/`.
`src/goldilocks_agent/core_pin.json` records the tarball, its sha256 and the
core release the backend runs; `tests/test_core_pin.py` fails until they
agree. To move to a new core release:

1. `uv run poe bump-core vX.Y.Z` (Node >= 24): builds the tarball from that
   tag and rewrites the pin.
2. Re-record the DFT Workbench test fixtures against a core of that release:
   `node scripts/record-core-fixtures.mjs` in `app/` (see its header). The
   vendored client refuses responses from any other vocabulary.

The website's own upgrade steps are in
[`deploy/stfc-cloud/README.md`](https://github.com/stfc/goldilocks-agent/blob/main/deploy/stfc-cloud/README.md).

### MLIP Playground

Off by default when running from source:
`export GOLDILOCKS_AGENT_MLIP_ENABLED=1`. The first calculation runs a
multi-GB `uv sync` in `mlip-cli/`.

### Local-only and coming-soon Tools

`TOOLS` in `app/src/App.tsx` marks each Tool. `localOnly` ones (MLIP
Playground, AiiDAlab-QE) are listed on the website as available in the local
version; the server also hides MLIP Playground's `run_mlip_*` tools from the
website's model and refuses its `/api/mlip/*` routes
(`mlip_playground_offered()` in `src/goldilocks_agent/tools/__init__.py`).
`comingSoon` parks a Tool everywhere (Beyond DFT); drop the flag to
re-enable it.

## Docker images

`.github/workflows/docker-publish.yml` builds two images from `main` on every
push, tagged `latest` and `sha-<commit>`:

- `goldilocks-agent-local` (linux/amd64 and linux/arm64), run by the
  repository's `docker-compose.yml`;
- `goldilocks-agent-web` (linux/amd64), the same source with the website's
  frontend build (`npm run build:stfc-cloud`), run by
  `deploy/stfc-cloud/docker-compose.yml`.

The image also builds enumlib's `enum.x` and `makeStr.py`, which core needs
to enumerate antiferromagnetic orderings; without them core silently lists
only the ferromagnetic one, with a warning.

`docker compose up --build` builds the local image from your clone instead of
pulling it. A fork's workflow publishes under its own owner; point the compose
file at it, or at one commit's build, in `.env`:

```bash
GOLDILOCKS_AGENT_IMAGE_OWNER=<fork owner>
GOLDILOCKS_AGENT_IMAGE_TAG=sha-xxxxxxx
```

## Tests

```bash
uv run poe check       # ruff lint and format gate, backend tests
uv run poe web-check   # frontend lint, type check, tests, build
```

Tests that need real credentials or services are marked `integration`, each
with its own `skipif`, so they show as skipped when nothing is configured.
Configure what they need (an API key, an Ollama model,
`GOLDILOCKS_AGENT_MLIP_ENABLED`, ...) and they run;
`uv run pytest -m integration` selects only them. The web build's end-to-end
tests, run before each website rollout, are described in
[`app/e2e/README.md`](https://github.com/stfc/goldilocks-agent/blob/main/app/e2e/README.md).
