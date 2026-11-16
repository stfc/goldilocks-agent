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
On 2026-11-12 `dft_settings`/`dft_set`/`dft_magnetic_orderings` joined
them, so the chat can read and change every setting the panel shows. On
2026-11-16 all six moved to the server (#142): they change the chat's own
draft there and call goldilocks-core over HTTP, and the panel replays the
draft -- still one state, now kept by the server, testable without a page.

This module is the single place `graph.py` reads tools from -- it never
reaches into a specific Tool's internals, so adding a new Tool later is "add
one import + spread it into these collections, and name it in the profiles
that offer it (`agent_profile.py`)," not a `graph.py` edit. That includes:
- `CONFIRMATION_REQUIRED_TOOLS`/`CONFIRMATION_LABELS` (design doc 17.3's
  interrupt mechanism, first wired up for MLIP Playground): any tool name in
  that set gets gated through a confirmation card in `graph.py`'s
  `call_tool` before it ever runs, generic to whichever Tool declares it.
- `CONTEXT_TOOLS` (first wired up for DFT Workbench): any tool name in that
  set is called with a `tools.context.ToolContext` first -- its chat's id
  and messages, a way to send the browser an event, the chat database and
  the goldilocks-core client -- generic to whichever Tool declares it.

A shipped Tool can also be parked as "Coming soon" without deleting it
(2026-10-09, MLIP Playground): its subpackage, tests and registry entries
stay intact, and leaving it out of every profile in `agent_profile.py`
keeps the LLM from being offered a tool whose UI panel can't be opened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.dft_workbench.tool import (
    CONFIRMATION_LABELS as _DFT_CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED as _DFT_CONFIRMATION_REQUIRED,
    CONTEXT_TOOLS as _DFT_CONTEXT_TOOLS,
    ORIGIN_AUDITED as _DFT_ORIGIN_AUDITED,
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


def _names(schemas: list[dict]) -> tuple[str, ...]:
    return tuple(schema["function"]["name"] for schema in schemas)


# Each Tool's chat tools, in schema order. Which a deployment offers is
# `agent_profile.py`'s choice, not this registry's: every tool stays
# registered here, and `graph.call_tool` refuses one the profile didn't
# offer before reaching its dispatch or confirmation entries.
STRUCTURE_SEARCH_TOOLS = _names(_STRUCTURE_SEARCH_SCHEMA)
MLIP_PLAYGROUND_TOOLS = _names(_MLIP_SCHEMA)
DFT_WORKBENCH_TOOLS = _names(_DFT_SCHEMA)

TOOL_SCHEMAS: dict[str, dict] = {
    schema["function"]["name"]: schema
    for schema in (*_STRUCTURE_SEARCH_SCHEMA, *_MLIP_SCHEMA, *_DFT_SCHEMA)
}

TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    **_STRUCTURE_SEARCH_DISPATCH,
    **_MLIP_DISPATCH,
    **_DFT_DISPATCH,
}
# structure_search/DFT Workbench have no confirmation-required tools --
# only MLIP Playground contributes to these two sets so far.
CONFIRMATION_REQUIRED_TOOLS: set[str] = {
    *_MLIP_CONFIRMATION_REQUIRED,
    *_DFT_CONFIRMATION_REQUIRED,
}
CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {
    **_MLIP_CONFIRMATION_LABELS,
    **_DFT_CONFIRMATION_LABELS,
}
# Only DFT Workbench's tools work on the chat itself so far -- MLIP Playground
# and structure_search take their arguments alone.
CONTEXT_TOOLS: set[str] = {*_DFT_CONTEXT_TOOLS}
# Tools whose tool message records the user message the turn answers
# (design doc 3.3's `origin_message_id`), injected by `call_tool`.
ORIGIN_AUDITED_TOOLS: set[str] = {*_DFT_ORIGIN_AUDITED}
