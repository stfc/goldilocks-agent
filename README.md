# goldilocks-agent

LLM orchestration, feedback-driven scientific workflow, and AiiDA execution
layer for [`goldilocks-core`](https://github.com/stfc/goldilocks-core). Core
decides DFT parameters; this package decides *when to ask the user*, *how to
explain a recommendation*, and *how to run it* (bundle download or AiiDA
submission).

> **Status**: real, working chat + tool-calling for three of the six
> planned Tools -- **Find in Databases** (Materials Project/Materials
> Cloud/NOMAD/JARVIS search), **MLIP Playground** (local MACE calculations
> via `janus-core`), and **DFT Workbench** (real Quantum ESPRESSO input
> generation via `goldilocks-core`). **Beyond DFT**/**Post Analysis** are
> partial (panel-only, no real backing yet beyond Post Analysis's phonon
> visualizer). **AiiDA** is not built. See
> [`docs/goldilocks-agent-design.md`](docs/goldilocks-agent-design.md) for
> the full product design and
> [`docs/goldilocks-agent-implementation-plan.md`](docs/goldilocks-agent-implementation-plan.md)
> for a dated log of what's actually been built vs. still planned.

## Layout

```
src/goldilocks_agent/   Python package (LangGraph orchestration, local HTTP/SSE server, Tools)
app/                    React/Vite frontend (chat + Tools panel; DFT Workbench's own
                        full-page detail embeds goldilocks-core/web's published UI --
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
Workspace).

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

**Optional: DFT Workbench** (real Quantum ESPRESSO input generation via
`goldilocks-core`) -- point at your own `goldilocks-core` checkout (this
repo doesn't vendor it, since it's your own separate, actively-developed
project):

```bash
export GOLDILOCKS_CORE_PATH=/path/to/your/goldilocks-core
```

Both `client.py`s shell out via `uv run --project <path> <cli>` on each
call rather than running a persistent service, so nothing needs to stay
running in the background for either.

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

**DFT Workbench's full-page detail**: expand the Tools panel's own
expand-all-tools button (top right, next to the Tools panel toggle) to see
all six Tools full-page, then expand DFT Workbench there -- its detail page
embeds goldilocks-core's own Workbench UI, published as an npm package and
installed into `app/`'s dependencies (see
[issue #1](https://github.com/junwen94/goldilocks-agent/issues/1)). It talks
to core's HTTP server directly, not via the CLI path `GOLDILOCKS_CORE_PATH`
configures above -- start that server too:

```bash
cd /path/to/goldilocks-core && uv run poe serve   # core backend on http://127.0.0.1:8000
```

`app/vite.config.js` proxies `/capabilities`, `/run`, `/explain`, etc. to
that port; without it running, DFT Workbench's detail page still renders but
shows a "Request failed" banner instead of real data. The published-package
pipeline is currently a local tarball
(`app/vendor/goldilocks-workbench-0.0.0.tgz`, rebuilt from `core/web` via
`npm run build:lib && npm pack`) rather than a real registry -- see issue #1
for the GitHub Packages follow-up.

## Usage

Start a chat and either talk to the model directly, or open one of the
Tool panels on the right (the small icon strip) for a structured
interface into the same underlying capability -- both paths call the same
backend functions, and the panel updates live when the model calls a tool
on its own.

- **Find in Databases**: search by chemical formula, or type/attach a
  structure and ask about it directly (`find_in_databases`/`get_structure`
  tools, no confirmation needed -- it's a read-only lookup).
- **MLIP Playground**: single-point/geometry-optimization/equation-of-
  state/NEB/phonon calculations via MACE. Every calculation -- from the
  chat or from the panel's own buttons -- asks for explicit confirmation
  first, since it's real local compute (`GOLDILOCKS_AGENT_MLIP_ENABLED`
  must be set). Phonon results link to a "Phonon visualizer" -- also
  reachable from Post Analysis for a `band.yaml` from anywhere else.
- **DFT Workbench**: pick a structure, code, and task, optionally override
  specific settings, then Generate -- goldilocks-core's real advisors
  resolve everything else and produce a downloadable Quantum ESPRESSO
  input bundle (input file, pseudopotential, SLURM submission script). The
  Explain tab shows *why* each setting was chosen. The same thing is
  callable from chat via `dft_explain`/`dft_generate` (`GOLDILOCKS_CORE_PATH`
  must be set); no confirmation needed -- it's local file generation, not
  compute.
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
`GOLDILOCKS_AGENT_MLIP_ENABLED`, `GOLDILOCKS_CORE_PATH`, ...) and they run
for real -- see each test module's own skip reason for exactly what it
needs. Select only those with `uv run pytest -m integration`.
