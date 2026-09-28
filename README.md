# goldilocks-agent

LLM orchestration, feedback-driven scientific workflow, and AiiDA execution
layer for [`goldilocks-core`](https://github.com/stfc/goldilocks-core). Core
decides DFT parameters; this package decides *when to ask the user*, *how to
explain a recommendation*, and *how to run it* (bundle download or AiiDA
submission).

> **Status**: real, working chat + tool-calling for two of the six planned
> Tools -- **Find in Databases** (Materials Project/Materials Cloud/NOMAD/
> JARVIS search) and **MLIP Playground** (local MACE calculations via
> `janus-core`). **DFT Workbench** is real too, but not via chat
> tool-calling -- it embeds `goldilocks-core`'s own published Workbench UI
> directly (same content inline, as tabs, and full-page, as a grid), talking
> to a `goldilocks-core` HTTP backend goldilocks-agent auto-starts for you
> (see Configuration below). **Beyond DFT**/**Post Analysis** are partial
> (panel-only, no real backing yet beyond Post Analysis's phonon
> visualizer). **AiiDA** is not built. See
> [`docs/goldilocks-agent-design.md`](docs/goldilocks-agent-design.md) for
> the full product design and
> [`docs/goldilocks-agent-implementation-plan.md`](docs/goldilocks-agent-implementation-plan.md)
> for a dated log of what's actually been built vs. still planned.

## Layout

```
src/goldilocks_agent/   Python package (LangGraph orchestration, local HTTP/SSE server, Tools)
app/                    React/Vite frontend (chat + Tools panel; DFT Workbench's inline panel
                        and full-page detail both embed goldilocks-core/web's published UI --
                        see issue #1) -- not "web": see design doc §13
mlip-cli/               Own project (own pyproject.toml), just a `janus-core[mace]` dependency pin --
                        keeps torch/mace out of goldilocks-agent's own env; MLIP Playground shells
                        out to `janus` (janus-core's own CLI) inside it, see tools/mlip_playground/
tests/
docs/                   Design documents
```

## Installation

Two ways to get this running: Docker (fewest steps, no Python/Node
toolchain needed) or a manual `uv`/`npm` install. Either way, pick a
conversation engine first -- this choice is independent of Docker vs
manual, and the two options never share a code path (an API key never
routes through Ollama, and vice versa):

- **Cloud API key (fastest, recommended for a quick try)** -- get a key
  from Anthropic/OpenAI/Gemini and set it as an environment variable, or
  add it later from the app's own Settings panel. No download, no GPU
  needed, nothing else to install. See Configuration below for the env var
  names.
- **Local model via Ollama (private -- nothing leaves your machine, but a
  ~16GB one-time download)** -- install [Ollama](https://ollama.com)
  natively on your own machine, *not* in a container -- 2026-09-25,
  confirmed live that Docker Desktop gives containers no GPU/Metal
  passthrough, so a *containerized* Ollama running `qwen3.8` (a real
  27B-parameter model despite the name) is CPU-only and dramatically slower
  (measured: a single reply took ~3 minutes, sometimes timing out before
  finishing, vs. ~20 seconds native with Metal) -- this repo deliberately
  doesn't run Ollama in a container on either path:

  ```bash
  # Install from https://ollama.com, then:
  ollama pull qwen3.8
  ```

  `qwen3.8` is ~16GB on disk and needs comparable RAM headroom to load --
  see the RAM note in [`docs/getting-started.md`](docs/getting-started.md)
  if your first chat message gets its inference process killed instead of
  replying.

### Docker

Requires [Docker](https://docs.docker.com/get-docker/) (with Compose,
included in current Docker Desktop/Engine installs) and whichever
conversation engine you picked above. Only one file needed, no clone:

```bash
mkdir goldilocks-agent && cd goldilocks-agent
curl -O https://raw.githubusercontent.com/stfc/goldilocks-agent/main/docker-compose.yml
```

**Using a cloud API key?** Drop it in a `.env` file next to
`docker-compose.yml` before starting -- Compose reads this automatically,
no need to edit the YAML itself:

```bash
cat > .env <<'EOF'
ANTHROPIC_API_KEY=<your key>
EOF
```

(swap in `OPENAI_API_KEY`/`GEMINI_API_KEY` for those providers -- see
Configuration below for the full list.) Skipping this is fine too: leave it
unset for now and add the key later from the running app's own Settings
panel instead. Going the local-Ollama route needs no `.env` at all.

```bash
docker compose up
```

This pulls the published `agent` image
(`ghcr.io/stfc/goldilocks-agent:latest` by default, built on every push to
`main`) rather than building locally, so it's fast even on a machine with
no Python/Node toolchain -- `build: .` in `docker-compose.yml` is only a
local-dev fallback (`docker compose up --build` forces a rebuild from
source, which does need a real clone). Add
`GOLDILOCKS_AGENT_IMAGE_OWNER`/`GOLDILOCKS_AGENT_IMAGE_TAG` to that same
`.env` if you want `junwen94`'s build instead, or a specific commit's
`sha-xxxxxxx` tag rather than `latest` (only useful for reproducing one
exact build -- for everyday use, `latest` already tracks the newest
successful publish automatically, no manual bumping needed). Both repos
publish from the same source, just to different `ghcr.io` namespaces. Once
you've picked an engine, open <http://localhost:8080>.

What's on by default in this image, no extra config needed: chat, **Find in
Databases**, **MLIP Playground** (real local MACE calculations), **DFT
Workbench** (goldilocks-core's own real Workbench UI, embedded), its
**magnetism ML tier (mMACE)**, and real AFM magnetic-ordering enumeration
(`enum.x`/`makeStr.py`, built from source the same way
[stfc/goldilocks-core#227](https://github.com/stfc/goldilocks-core/pull/227)
builds them for that repo's own image -- without this, goldilocks-core
silently degrades to FM-only with a warning instead of erroring, so it's
easy to not notice it's missing). The first time you actually open DFT
Workbench, expect a one-time multi-GB download (goldilocks-core's own
dependencies plus mMACE's checkpoint/packages) before it's ready -- chat and
Find in Databases work immediately regardless, and this cost is cached
across `docker compose down`/`up` (named volumes, not container state) so
it only happens once. See
[`docs/getting-started.md`](docs/getting-started.md) for the fuller
walkthrough (RAM requirements, what needs extra setup, persistence
behavior).

### Manual (`uv` + `npm`)

Prerequisites: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Node.js
18+/npm, Ollama (above, optional if you're only using a cloud model). Also
optional: a [goldilocks-core](https://github.com/stfc/goldilocks-core)
checkout (DFT Workbench).

```bash
git clone <this-repo> && cd goldilocks-agent
uv sync --group dev        # backend: Python deps into ./.venv
cd app && npm install      # frontend: JS deps into app/node_modules
```

## Configuration

All of this is optional at first launch -- the app runs and tells you
clearly (a 503 with a specific message, not a crash) if you try to use
something that isn't configured yet.

**LLM providers** -- set whichever you want to use, either as an
environment variable before starting the server, or later from the app's
own Settings panel (saved to `~/.config/goldilocks/config.toml`, mode
`0600`; the env var always wins if both are set):

| Provider | Env var |
|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Google (Gemini) | `GEMINI_API_KEY` |
| Materials Project (structure search, not an LLM) | `MP_API_KEY` |

**Local model** (no API key, runs on your own machine): `ollama pull
qwen3.8` natively, per Installation above -- it's what the model selector's
"Local" group uses, and is vision-capable. Override the resolved model
entirely with `GOLDILOCKS_AGENT_MODEL` (a litellm model string, e.g.
`anthropic/claude-sonnet-5`) if you want to force a specific one.

You need at least one of the two above -- a cloud API key or a local
Ollama model -- for chat to actually respond; everything below this point
is opt-in for extra Tools, not required to start using the app at all.

**Optional: MLIP Playground** (local MACE calculations via `janus-core`) --
off by default (the first real calculation triggers a multi-GB `uv sync`
inside `./mlip-cli/`, so this needs to be opt-in):

```bash
export GOLDILOCKS_AGENT_MLIP_ENABLED=1
```

**Optional: DFT Workbench** (goldilocks-core's own real Workbench UI,
embedded directly) -- `goldilocks-core` is [on PyPI](https://pypi.org/project/goldilocks-core/)
now, so the simplest way to enable this needs no checkout at all:

```bash
export GOLDILOCKS_AGENT_CORE_AUTOSTART=1
```

If you're actively developing goldilocks-core itself, point at your local
checkout instead -- it takes priority over the PyPI package, so you get
your own uncommitted changes. The checkout needs its `http` extra synced
first (`fastapi`/`uvicorn` aren't in core's base install -- the PyPI path
above doesn't need this step because `goldilocks-core[http]` already
requests it):

```bash
cd /path/to/your/goldilocks-core && uv sync --extra http
export GOLDILOCKS_CORE_PATH=/path/to/your/goldilocks-core
```

Unlike MLIP Playground's per-call `janus` CLI shell-out, this one *is* a
persistent service -- the first time you actually open DFT Workbench
(inline or full-page), goldilocks-agent lazily runs it for you (either
`uvx --from goldilocks-core[http] goldilocks serve http`, or your
checkout's own `uv run --directory <path> poe serve` if
`GOLDILOCKS_CORE_PATH` is set), health-checking first so it reuses an
instance you already started yourself in another terminal instead of
double-spawning, and keeps it running for the rest of the session,
shutting it down when goldilocks-agent's own process exits. Nothing to
start by hand either way.

**Optional: magnetism ML tier (mMACE)** -- upgrades two of DFT Workbench's
magnetism fields (`is_magnetic` classification, magnetic-ordering ranking)
from a heuristic/LLM tier to a real ML model. This is entirely
goldilocks-core's feature (model, checkpoint, and classification logic all
live there) -- goldilocks-agent only auto-starts the process that serves
it, the same as plain DFT Workbench above. Requires **checkout mode**
(`GOLDILOCKS_CORE_PATH`) -- the PyPI/`GOLDILOCKS_AGENT_CORE_AUTOSTART` path
can't host this, because the `mace` fork it needs has no PyPI release and
can only be installed into a real venv you control:

```bash
# 1. In your goldilocks-core checkout (same one GOLDILOCKS_CORE_PATH points
#    at, with its `http` extra already synced per above)
cd /path/to/your/goldilocks-core
uv pip install ase==3.28.0 e3nn==0.4.4 sphericart==1.0.9 sphericart-torch==1.0.9
uv pip install "mace-torch @ git+https://github.com/CheukHinHoJerry/mace.git@19cdf6692c48e068a24e06cfe1ffc670e8aea3dd"

# 2. One-time checkpoint download
mkdir -p ~/.local/share/goldilocks/mmace
curl -L -o ~/.local/share/goldilocks/mmace/mace_matpes_pbe_baseline_run-3.model \
  https://data-collections.psdi.ac.uk/api/records/1g8rw-q8128/files/mace_matpes_pbe_baseline_run-3.model/content

# 3. Back in goldilocks-agent, same shell, before starting the backend
export GOLDILOCKS_CORE_PATH=/path/to/your/goldilocks-core
export GOLDILOCKS_MACE_BACKBONE=~/.local/share/goldilocks/mmace/mace_matpes_pbe_baseline_run-3.model
uv run poe serve
```

Open DFT Workbench and load a magnetic structure (e.g. goldilocks-core's
own `src/goldilocks_core/examples/structures/Fe_bcc.cif`) -- the Analysis
card's "is magnetic" caption switches to "Goldilocks-ML prediction" once
the ML tier is live.

One thing worth knowing: running a plain `uv sync` (with or without extra
flags) in the checkout wipes the two manually-installed packages again --
they're not in `uv.lock`, and never can be (PyPI rejects a git dependency
in a published package's metadata outright). Rerun step 1 above after any
`uv sync` there. goldilocks-agent's own auto-spawn (`uv run --directory
<path> poe serve`) does *not* trigger this, only a `uv sync` you run
yourself.

Don't confuse this with **MLIP Playground** above -- different `mace`
package (this public fork vs. `janus-core`'s own `[mace]` extra), different
purpose (magnetism classification/ranking vs. general-purpose
single-point/EOS/NEB/phonon calculations), different checkpoint, no shared
configuration.

## Running it

```bash
uv run poe serve            # backend on http://127.0.0.1:8080
cd app && npm run dev        # frontend on http://localhost:5173, proxies /api to :8080
```

Open `http://localhost:5173`. The first run also kicks off a one-time
background download of the JARVIS DFT-3D dataset (~200MB, needed for Find
in Databases' JARVIS results) -- the app is usable immediately, JARVIS
results just start working once it finishes. Run it manually ahead of
time with `uv run poe fetch-jarvis-cache` if you'd rather not wait.

**DFT Workbench**: open its panel (inline, in the Tools strip) or expand it
full-page (the Tools panel's own expand-all-tools button, top right, then
DFT Workbench from the six-Tool grid) -- both render the exact same
embedded goldilocks-core Workbench UI, published as an npm package and
installed into `app/`'s dependencies (see
[issue #1](https://github.com/junwen94/goldilocks-agent/issues/1)): the
full-page view shows all its cards (Structure/Analysis/Advisors/Bundles)
side by side, the inline panel shows the same cards as tabs, one at a time.
The first time either is opened, goldilocks-agent auto-starts
goldilocks-core's own HTTP backend on `http://127.0.0.1:8000` for it to
talk to (see Configuration above) -- you'll briefly see a "starting up..."
state while that happens, or a clear error if neither
`GOLDILOCKS_AGENT_CORE_AUTOSTART` nor `GOLDILOCKS_CORE_PATH` is set. The published-package
pipeline is currently a local tarball
(`app/vendor/goldilocks-workbench-0.0.4.tgz`, rebuilt from `core/web` via
`npm run build:lib && npm pack`) rather than a real registry -- see issue #1
for the GitHub Packages follow-up.

## Usage

Start a chat and either talk to the model directly, or open one of the
Tool panels on the right (the small icon strip) for a structured
interface into the same underlying capability -- for Find in Databases and
MLIP Playground, both paths call the same backend functions, and the panel
updates live when the model calls a tool on its own. DFT Workbench is the
exception: it has no chat tool-calling of its own (see below) -- Goldilocks
in chat only ever gives DFT guidance, the real setup/results live entirely
in its embedded panel.

- **Find in Databases**: search by chemical formula, or type/attach a
  structure and ask about it directly (`find_in_databases`/`get_structure`
  tools, no confirmation needed -- it's a read-only lookup).
- **MLIP Playground**: single-point/geometry-optimization/equation-of-
  state/NEB/phonon calculations via MACE. Every calculation -- from the
  chat or from the panel's own buttons -- asks for explicit confirmation
  first, since it's real local compute (`GOLDILOCKS_AGENT_MLIP_ENABLED`
  must be set). Phonon results link to a "Phonon visualizer" -- also
  reachable from Post Analysis for a `band.yaml` from anywhere else.
- **DFT Workbench**: the embedded goldilocks-core Workbench itself handles
  structure input, analysis, advisors, and bundle download/generation --
  talk to it directly, not through chat (`GOLDILOCKS_AGENT_CORE_AUTOSTART`
  or `GOLDILOCKS_CORE_PATH` must be set; goldilocks-agent auto-starts
  core's backend for you either way, see Configuration above). Chat can
  still explain DFT concepts/workflows in general, it just can't drive this
  Tool's panel the way it drives Find in Databases/MLIP Playground. Its
  magnetism fields can additionally run on a real ML tier (mMACE) instead
  of heuristic/LLM -- separate opt-in setup, see Configuration above.
- Drag a structure file (CIF/XYZ/POSCAR/VASP/XSF/CUBE) onto the window, or
  use a Tool panel's own "Upload structure" button, to add it to the
  current chat.

## Development

```bash
uv run poe check     # lint + backend test suite
uv run poe web-check  # lint + build the frontend
```

Tests that need real credentials/local services are marked `integration`
and each has its own `skipif` -- they show up as *skipped*, not deselected,
in a plain `uv run poe check` run with nothing configured. Set whichever
they need (a configured API key, a running Ollama model,
`GOLDILOCKS_AGENT_MLIP_ENABLED`, ...) and they run
for real -- see each test module's own skip reason for exactly what it
needs. Select only those with `uv run pytest -m integration`.
