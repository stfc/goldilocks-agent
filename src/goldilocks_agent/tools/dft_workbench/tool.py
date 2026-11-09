"""LLM-facing tool wrappers for DFT Workbench.

Unlike `mlip_playground/tool.py`, these three functions never actually run
-- `CLIENT_EXECUTED` (aggregated into `graph.py`'s `CLIENT_EXECUTED_TOOLS`)
always intercepts them in `call_tool` before `TOOL_DISPATCH` would be
consulted. The real work happens in the browser: `app/src/dftWorkbench.ts`'s
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

# A name, never the file's text: re-typing a structure into a tool call is
# slow, and smaller models silently truncate or alter it (#73). The browser
# looks the name up among this chat's structure files (see
# `app/src/dftWorkbench.ts`'s `dispatchDftTool`).
_FILE_NAME_PARAM = {
    "type": "string",
    "description": (
        "The exact name of a structure file in this chat: a file the user "
        "attached (the NAME in '[Attached file: NAME]') or the filename a "
        "get_structure call returned. DFT Workbench reads the file itself, "
        "so never pass the file's content."
    ),
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
                "Open a structure file from this chat, by its name, in DFT "
                "Workbench, the embedded goldilocks-core panel -- required "
                "before dft_review or "
                "dft_download_bundle can be called. Parses and validates the "
                "structure; no DFT parameters are chosen yet. Returns a "
                "summary of the structure (partially occupied sites must be "
                "ordered before goldilocks-core recommends anything), the "
                "calculation it opened into (code, task, HPC profile and "
                "overrides, kept from the previous structure except the "
                "structure-specific overrides listed in cleared_overrides), "
                "and the tasks goldilocks-core offers."
            ),
            "parameters": {
                "type": "object",
                "properties": {"file_name": _FILE_NAME_PARAM},
                "required": ["file_name"],
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
                "the structure and settings currently open in DFT Workbench "
                "(must call dft_open_structure first). Read-only, writes no "
                "files. Returns each record's value and the source that "
                "decided it, the warnings, and `blocking`: decisions "
                "goldilocks-core needs before it can generate input files, "
                "each with remedies (override patches that resolve it). "
                "Settings are changed in the DFT Workbench panel; ask the "
                "user before suggesting a remedy be applied."
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
                "input files) for the structure and settings currently open "
                "in DFT Workbench (must call dft_open_structure first; it "
                "reviews the current settings itself). When goldilocks-core "
                "blocks generation, the error names the decisions it needs "
                "and their remedies: ask the user instead of calling this "
                "again unchanged. No separate confirmation step -- explain what "
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
# themselves click a Download button, which browsers already force to be a
# direct, unavoidable click (see app/src/dftWorkbench.ts's dispatchDftTool).
# A chat-level "Run it" card ahead of that would just be a second, redundant
# click gating nothing real -- 2026-09-25, removed after exactly that
# friction was flagged in real use.
CONFIRMATION_REQUIRED: set[str] = set()

CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {}
