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
*Coming soon* this release, so this has no effect until it's re-enabled
(see [Tools](tools.md)). Off by default (the first real calculation
triggers a multi-GB `uv sync` inside `./mlip-cli/`, so this needs to be
opt-in):

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

The checkout has to speak the same `vocabulary_version` as the embedded
Workbench UI (the one in `src/goldilocks_agent/core_pin.json`; a checkout
reports its own in `src/goldilocks_core/capabilities.py`) -- otherwise
goldilocks-agent reports an error naming both vocabulary versions instead
of loading DFT Workbench.

Unlike MLIP Playground's per-call `janus` CLI shell-out, this one *is* a
persistent service -- the first time something needs it (opening DFT
Workbench inline or full-page, a chat DFT tool, or reloading a page with a
saved DFT Workbench state), goldilocks-agent lazily runs it for you (either
`uvx --from goldilocks-core[http]==<version> goldilocks serve http`,
with `<version>` the core release pinned in
`src/goldilocks_agent/core_pin.json`, or your
checkout's own `uv run --directory <path> poe serve` if
`GOLDILOCKS_CORE_PATH` is set), health-checking first so it reuses an
instance you already started yourself in another terminal instead of
double-spawning, and keeps it running for the rest of the session,
shutting it down when goldilocks-agent's own process exits. Nothing to
start by hand either way. Its output, including the traceback behind any
HTTP 500, goes to goldilocks-agent's own log prefixed `[goldilocks-core]`
(`docker compose logs agent` in Docker).

**Magnetism ML tier (mMACE)** -- goldilocks-core's ML `is_magnetic`
classification and magnetic-ordering ranking. Since goldilocks-core v0.3.0
it needs no setup of its own, in either mode: `mace-torch` is an ordinary
core dependency, and `goldilocks assets install all` (the profile was
`workbench` before core v0.3.1), which the auto-start runs before serving,
installs the model's backbone with core's
other models. Open DFT Workbench and load a magnetic structure (e.g.
goldilocks-core's own `src/goldilocks_core/examples/structures/Fe_bcc.cif`)
-- the Analysis card's "is magnetic" caption reads "Goldilocks-ML
prediction".

Upgrading from an earlier setup:

- Unset `GOLDILOCKS_MACE_BACKBONE` if it still points at the v1.0 checkpoint
  (`mace_matpes_pbe_baseline_run-3.model`). It overrides the installed
  backbone, and core refuses the v1.0 file, so every `is_magnetic` request
  fails.
- A checkout with the `CheukHinHoJerry/mace` fork or `sphericart` installed
  by hand gets its locked `mace-torch` back on the next
  `uv sync --extra http` there.
- The first start after the upgrade fetches the changed models and the
  PseudoDojo tables again (from PSDI), which can take many minutes; DFT
  Workbench says so, with core's latest output, while it runs.

goldilocks-core's `docs/mmace-setup.md` has the details.

Don't confuse this with **MLIP Playground** above -- different `mace`
package (core's own `mace-torch` vs. `janus-core`'s `[mace]` extra), different
purpose (magnetism classification/ranking vs. general-purpose
single-point/EOS/NEB/phonon calculations), different checkpoint, no shared
configuration.
