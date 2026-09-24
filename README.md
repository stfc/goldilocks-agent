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

> Just want to run the app, not develop it? See
> [`docs/getting-started.md`](docs/getting-started.md) for a Docker-based
> walkthrough (fewest steps) or a plainer version of the manual install
> below.

Prerequisites: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Node.js
18+/npm. Optional, only if you want the features they back: a local
[Ollama](https://ollama.com) install (local-model chat), a
[goldilocks-core](https://github.com/stfc/goldilocks-core) checkout (DFT
Workbench).

```bash
git clone <this-repo> && cd 1-goldilocks-agent
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

**Local model** (no API key, runs on your own machine): install
[Ollama](https://ollama.com), then

```bash
ollama pull qwen3.8
```

`qwen3.8` is vision-capable and is what the model selector's "Local"
group uses. Despite the name, it's a 27B-parameter model (~16GB on disk) --
make sure you have comparable RAM headroom free, or the first real chat
message will get its inference process killed rather than replying (see
[`docs/getting-started.md`](docs/getting-started.md) if you hit this via
Docker specifically). Override the resolved model entirely with
`GOLDILOCKS_AGENT_MODEL` (a litellm model string, e.g.
`anthropic/claude-sonnet-5`) if you want to force a specific one.

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
your own uncommitted changes:

```bash
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
(`app/vendor/goldilocks-workbench-0.0.0.tgz`, rebuilt from `core/web` via
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
  Tool's panel the way it drives Find in Databases/MLIP Playground.
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
