# Configuration

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
qwen3.8` natively, per [Getting started](getting-started.md) -- it's what
the model selector's "Local" group uses, and is vision-capable. Override
the resolved model entirely with `GOLDILOCKS_AGENT_MODEL` (a litellm model
string, e.g. `anthropic/claude-sonnet-5`) if you want to force a specific
one.

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
embedded directly) -- `goldilocks-core` is
[on PyPI](https://pypi.org/project/goldilocks-core/) now, so the simplest
way to enable this needs no checkout at all:

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
