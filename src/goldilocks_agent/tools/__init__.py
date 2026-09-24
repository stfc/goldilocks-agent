"""The agent's Tools -- one subpackage per entry in the UI's six-Tool list
(design doc 十二: Find in Databases, DFT Workbench, MLIP Playground, Beyond
DFT, Post Analysis, AiiDA). Each subpackage owns both its implementation and
its LLM-facing tool schema together, so the schema can't drift from the
function it describes.

Only subpackages with a real, shipped implementation exist here -- the other
Tools are still UI-only placeholders (see implementation plan) and get their
subpackage the day their first real backend call lands, not before.

This module is the single place `graph.py` reads from -- it never reaches
into a specific Tool's internals, so adding a new Tool later is "add one
import + spread it into these collections," not a `graph.py` edit. That
includes `CONFIRMATION_REQUIRED_TOOLS`/`CONFIRMATION_LABELS` (design doc
17.3's interrupt mechanism, first wired up for MLIP Playground): any tool
name in that set gets gated through a confirmation card in `graph.py`'s
`call_tool` before it ever runs, generic to whichever Tool declares it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.dft_workbench.tool import (
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

TOOL_SCHEMAS: list[dict] = [*_STRUCTURE_SEARCH_SCHEMA, *_MLIP_SCHEMA, *_DFT_SCHEMA]
TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    **_STRUCTURE_SEARCH_DISPATCH,
    **_MLIP_DISPATCH,
    **_DFT_DISPATCH,
}
# DFT Workbench has no confirmation-required tools (see dft_workbench/tool.py's
# own docstring for why) -- same as structure_search, so neither contributes
# to these two sets, only MLIP Playground does.
CONFIRMATION_REQUIRED_TOOLS: set[str] = {*_MLIP_CONFIRMATION_REQUIRED}
CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {**_MLIP_CONFIRMATION_LABELS}
