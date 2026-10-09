"""The agent's Tools -- one subpackage per entry in the UI's six-Tool list
(design doc 十二: Find in Databases, DFT Workbench, MLIP Playground, Beyond
DFT, Post Analysis, AiiDA). Each subpackage owns both its implementation and
its LLM-facing tool schema together, so the schema can't drift from the
function it describes.

Only subpackages with a real, shipped implementation exist here -- the other
Tools are still UI-only placeholders (see implementation plan) and get their
subpackage the day their first real backend call lands, not before.

DFT Workbench history: 2026-09-24, the CLI-based `dft_workbench` package
(`dft_explain`/`dft_generate`, a subprocess-per-call implementation with its
own agent-owned side panel) was deleted entirely in favor of embedding
goldilocks-core's own published Workbench UI directly (see server.py's
`/api/core-server/*` auto-start route and `docs/goldilocks-agent-design.md`
for the merge decision), leaving DFT Workbench with no chat tool-calling at
all. One day later, `dft_workbench` came back in a different shape: its
three tools (`dft_open_structure`/`dft_review`/`dft_download_bundle`) are
`CLIENT_EXECUTED_TOOLS`, not server-dispatched -- `graph.py`'s `call_tool`
never calls their `TOOL_DISPATCH` entry (each just raises); instead it
`interrupt()`s a second time and treats the resume value as the tool's real
result, produced by `app/src/App.tsx` calling the exact same
`coreWorkspace.dispatch(...)` the embedded panel's own buttons call. This
keeps one state object shared between chat and the panel instead of
reintroducing the second, parallel implementation that was just deleted.

This module is the single place `graph.py` reads from -- it never reaches
into a specific Tool's internals, so adding a new Tool later is "add one
import + spread it into these collections," not a `graph.py` edit. That
includes:
- `CONFIRMATION_REQUIRED_TOOLS`/`CONFIRMATION_LABELS` (design doc 17.3's
  interrupt mechanism, first wired up for MLIP Playground): any tool name in
  that set gets gated through a confirmation card in `graph.py`'s
  `call_tool` before it ever runs, generic to whichever Tool declares it.
- `CLIENT_EXECUTED_TOOLS` (first wired up for DFT Workbench): any tool name
  in that set gets its result from a second `interrupt()`'s resume value
  instead of from `TOOL_DISPATCH`, generic to whichever Tool declares it. A
  tool can be in both sets, chaining two interrupts in one `call_tool` pass
  (confirm, then client-execute) -- `dft_download_bundle` did this briefly,
  but the confirmation card was removed 2026-09-25: it gated nothing real,
  since the browser already forces a genuine, unavoidable click on the
  Bundle card's own Download button before anything lands on the user's
  machine (see `dft_workbench/tool.py`'s `CONFIRMATION_REQUIRED` comment).

A shipped Tool can also be parked as "Coming soon" without deleting it
(2026-10-09, MLIP Playground): its subpackage and tests stay intact, but a
`<TOOL>_RELEASED = False` flag below keeps it out of every collection here,
so the LLM is never offered a tool whose UI panel can't be opened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.dft_workbench.tool import (
    CLIENT_EXECUTED as _DFT_CLIENT_EXECUTED,
    CONFIRMATION_LABELS as _DFT_CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED as _DFT_CONFIRMATION_REQUIRED,
    TOOL_DISPATCH as _DFT_DISPATCH,
    TOOL_SCHEMA as _DFT_SCHEMA,
)
from goldilocks_agent.tools.mlip_playground.tool import (
    CONFIRMATION_LABELS as _MLIP_CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED as _MLIP_CONFIRMATION_REQUIRED,
    TOOL_DISPATCH as _MLIP_DISPATCH,
    TOOL_SCHEMA as _MLIP_SCHEMA,
)
from goldilocks_agent.tools.structure_search.tool import (
    TOOL_DISPATCH as _STRUCTURE_SEARCH_DISPATCH,
    TOOL_SCHEMA as _STRUCTURE_SEARCH_SCHEMA,
)

# MLIP Playground is "Coming soon" this release (2026-10-09): App.tsx's
# TOOLS entry is marked `comingSoon`, so its panel is unreachable, and this
# flag keeps its `run_mlip_*` tools away from the LLM and makes server.py's
# `/api/mlip/*` routes 404. To bring it back: set this True *and* drop
# `comingSoon` from App.tsx's "ml-analysis" entry; the STFC Cloud web
# deployment additionally needs its MLIP lines uncommented in
# deploy/stfc-cloud/docker-compose.yml.
MLIP_PLAYGROUND_RELEASED = False

TOOL_SCHEMAS: list[dict] = [
    *_STRUCTURE_SEARCH_SCHEMA,
    *(_MLIP_SCHEMA if MLIP_PLAYGROUND_RELEASED else []),
    *_DFT_SCHEMA,
]
TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    **_STRUCTURE_SEARCH_DISPATCH,
    **(_MLIP_DISPATCH if MLIP_PLAYGROUND_RELEASED else {}),
    **_DFT_DISPATCH,
}
# structure_search/DFT Workbench have no confirmation-required tools --
# only MLIP Playground contributes to these two sets so far, so both are
# empty while it's unreleased.
CONFIRMATION_REQUIRED_TOOLS: set[str] = {
    *(_MLIP_CONFIRMATION_REQUIRED if MLIP_PLAYGROUND_RELEASED else set()),
    *_DFT_CONFIRMATION_REQUIRED,
}
CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {
    **(_MLIP_CONFIRMATION_LABELS if MLIP_PLAYGROUND_RELEASED else {}),
    **_DFT_CONFIRMATION_LABELS,
}
# Only DFT Workbench's tools are client-executed so far -- MLIP Playground
# and structure_search both run server-side via plain TOOL_DISPATCH.
CLIENT_EXECUTED_TOOLS: set[str] = {*_DFT_CLIENT_EXECUTED}
