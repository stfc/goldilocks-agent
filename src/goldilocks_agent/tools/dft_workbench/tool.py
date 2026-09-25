"""LLM-facing tool wrappers for DFT Workbench.

Unlike `mlip_playground/tool.py`, these three functions never actually run
-- `CLIENT_EXECUTED` (aggregated into `graph.py`'s `CLIENT_EXECUTED_TOOLS`)
always intercepts them in `call_tool` before `TOOL_DISPATCH` would be
consulted. The real work happens in the browser: `app/src/App.tsx`'s
`dispatchDftTool` calls the exact same `coreWorkspace.dispatch(...)` the
embedded `goldilocks-workbench` panel's own buttons call, so chat and the
panel share one state object instead of goldilocks-agent maintaining a
second, parallel implementation talking to goldilocks-core's HTTP API
(that second implementation existed once, as `dft_explain`/`dft_generate`,
and was deliberately deleted in favour of the embedded panel -- see
`tools/__init__.py`'s module docstring history).

`dft_review`/`dft_download_bundle` take no parameters: the calculation
context (code/task/hpc/overrides) lives in the embedded Workbench's own
`draft` state, set via the panel or a future `draft.patch`-backed tool --
not exposed here, since valid `task`/code ids are resolved dynamically
from goldilocks-core's `/capabilities` rather than being a fixed enum.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NoReturn

_STRUCTURE_CONTENT_PARAM = {
    "type": "string",
    "description": (
        "The structure's file content (CIF or POSCAR text), e.g. from a "
        "prior get_structure call or a file attached to this chat."
    ),
}
_STRUCTURE_NAME_PARAM = {
    "type": "string",
    "description": "A short filename for this structure, e.g. 'NaCl.cif'.",
}
_STRUCTURE_FORMAT_PARAM = {
    "type": "string",
    "enum": ["cif", "poscar"],
    "description": "Omit to infer from structure_name's extension.",
}


async def dft_open_structure(**_: object) -> NoReturn:
    raise RuntimeError(
        "dft_open_structure is client-executed (see CLIENT_EXECUTED_TOOLS in "
        "graph.py's call_tool) -- this stub must never be dispatched server-side."
    )


async def dft_review(**_: object) -> NoReturn:
    raise RuntimeError(
        "dft_review is client-executed (see CLIENT_EXECUTED_TOOLS in "
        "graph.py's call_tool) -- this stub must never be dispatched server-side."
    )


async def dft_download_bundle(**_: object) -> NoReturn:
    raise RuntimeError(
        "dft_download_bundle is client-executed (see CLIENT_EXECUTED_TOOLS in "
        "graph.py's call_tool) -- this stub must never be dispatched server-side."
    )


TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "dft_open_structure",
            "description": (
                "Open a structure in DFT Workbench, the embedded goldilocks-"
                "core panel -- required before dft_review or "
                "dft_download_bundle can be called. This only parses/"
                "validates the structure, no DFT parameters are chosen yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_CONTENT_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "structure_format": _STRUCTURE_FORMAT_PARAM,
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dft_review",
            "description": (
                "Preview goldilocks-core's DFT parameter recommendation for "
                "the structure currently open in DFT Workbench (must call "
                "dft_open_structure first). Read-only -- explains what would "
                "be generated, doesn't write any files."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dft_download_bundle",
            "description": (
                "Generate a real DFT input bundle (e.g. Quantum ESPRESSO "
                "input files) for the structure currently reviewed in DFT "
                "Workbench (must call dft_open_structure, then dft_review, "
                "first). No separate confirmation step -- explain what "
                "you're about to generate before calling this anyway, since "
                "it's a real generation step, but don't ask the user to "
                "click a 'confirm' button first. The bundle lands in the "
                "Workbench's Bundle card AND a Download button appears "
                "right here in the chat -- not on disk directly either way: "
                "tell the user to click one of those two Download buttons "
                "themselves to actually save the file, browsers don't allow "
                "a chat action to trigger a real file save on its own."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    "dft_open_structure": dft_open_structure,
    "dft_review": dft_review,
    "dft_download_bundle": dft_download_bundle,
}

CLIENT_EXECUTED: set[str] = set(TOOL_DISPATCH)

# No confirmation gate on any of these -- unlike MLIP Playground's real local
# compute, nothing here is consequential *to the user's machine* until they
# themselves click the Bundle card's Download button, which browsers already
# force to be a direct, unavoidable click (see App.tsx's dispatchDftTool
# comment). A chat-level "Run it" card ahead of that would just be a second,
# redundant click gating nothing real -- 2026-09-25, removed after exactly
# that friction was flagged in real use.
CONFIRMATION_REQUIRED: set[str] = set()

CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {}
