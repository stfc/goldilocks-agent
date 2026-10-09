# goldilocks-agent

LLM orchestration, feedback-driven scientific workflow, and AiiDA execution
layer for [`goldilocks-core`](https://github.com/stfc/goldilocks-core). Core
decides DFT parameters; this package decides *when to ask the user*, *how to
explain a recommendation*, and *how to run it* (bundle download or AiiDA
submission).

## Status

Real, working chat + tool-calling for **Find in Databases** (Materials
Project/Materials Cloud/NOMAD/JARVIS search). **DFT Workbench** is real
too, but not via chat tool-calling -- it embeds `goldilocks-core`'s own
published Workbench UI directly (same content inline, as tabs, and
full-page, as a grid), talking to a `goldilocks-core` HTTP backend
goldilocks-agent auto-starts for you (see
[Configuration](configuration.md)). **MLIP Playground** (local MACE
calculations via `janus-core`, built) and **Beyond DFT** (panel-only) are
parked under *Coming soon* this release (see [Tools](tools.md)). **Post
Analysis** is partial (only its phonon visualizer is real). **AiiDA** is
not built.

This site covers running and configuring goldilocks-agent. For the full
product design and a dated log of what's actually been built vs. still
planned, see
[`docs/goldilocks-agent-design.md`](https://github.com/junwen94/goldilocks-agent/blob/main/docs/goldilocks-agent-design.md)
and
[`docs/goldilocks-agent-implementation-plan.md`](https://github.com/junwen94/goldilocks-agent/blob/main/docs/goldilocks-agent-implementation-plan.md)
in the repository -- internal working documents, not part of this site.

## Layout

```
src/goldilocks_agent/   Python package (LangGraph orchestration, local HTTP/SSE server, Tools)
app/                    React/Vite frontend (chat + Tools panel; DFT Workbench's inline panel
                        and full-page detail both embed goldilocks-core/web's published UI)
mlip-cli/               Own project (own pyproject.toml), just a `janus-core[mace]` dependency pin --
                        keeps torch/mace out of goldilocks-agent's own env; MLIP Playground shells
                        out to `janus` (janus-core's own CLI) inside it
tests/
docs/                   This site, plus the internal design log
```

Start with [Getting started](getting-started.md).
