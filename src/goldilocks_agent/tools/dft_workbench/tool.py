"""LLM-facing tool wrappers for DFT Workbench.

Unlike `mlip_playground/tool.py`, these functions never actually run
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

The tools mirror the panel's own actions, grouped by intent (design doc
12.5): `dft_open_structure` opens (`source.open`), `dft_settings` reads every
setting the panel shows, `dft_set` changes any of them (`draft.patch`: the
Calculation form's code/task/HPC profile and every override control),
`dft_review`/`dft_download_bundle` review and generate, and
`dft_magnetic_orderings` lists magnetic orderings, with a ranking the user ran
in the panel (the chat doesn't rank: Junwen, 2026-11-16). Setting keys and
ids are free strings, not enums: the valid ones come from goldilocks-core's
`/capabilities` at runtime and the browser refuses an unknown one with the
closest valid keys (design doc 3.3's "an error that teaches").

`dft_set` is design doc 3.3's `relay`: values the user stated, which core
stamps `human`. The id of the user message they came from is injected by
`graph.py`'s `call_tool` (`ORIGIN_AUDITED`), never passed by the model.
Values the model chooses itself (`suggest`, the `llm` tier) wait on
goldilocks-core#258 (stfc/goldilocks-agent#67).
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


def _client_executed(name: str) -> Callable[..., Awaitable[NoReturn]]:
    """A TOOL_DISPATCH entry that must never run: `call_tool` hands a
    client-executed tool to the browser instead."""

    async def stub(**_: object) -> NoReturn:
        raise RuntimeError(
            f"{name} is client-executed (see CLIENT_EXECUTED_TOOLS in "
            "graph.py's call_tool) -- this stub must never be dispatched "
            "server-side."
        )

    stub.__name__ = name
    return stub


dft_open_structure = _client_executed("dft_open_structure")
dft_settings = _client_executed("dft_settings")
dft_set = _client_executed("dft_set")
dft_review = _client_executed("dft_review")
dft_download_bundle = _client_executed("dft_download_bundle")
dft_magnetic_orderings = _client_executed("dft_magnetic_orderings")


TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "dft_open_structure",
            "description": (
                "Open a structure file from this chat, by its name, in DFT "
                "Workbench, the embedded goldilocks-core panel -- required "
                "before dft_review, dft_download_bundle or "
                "dft_magnetic_orderings can be called. Parses and validates the "
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
            "name": "dft_settings",
            "description": (
                "List the settings DFT Workbench can change for the current "
                "calculation, as its panel shows them: the code, task and "
                "HPC profile with their options, then every parameter "
                "setting (functional, pseudopotentials, cutoffs, k-points, "
                "smearing, magnetism, job resources, ...) with its key, "
                "type, options, unit, current value and the source that "
                "decided it, and whether the user set it. Read-only. Call "
                "it to find the key for a setting before dft_set, or to "
                "show the user the options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "group": {
                        "type": "string",
                        "description": (
                            "Only this group's settings, e.g. 'k_sampling', "
                            "'occupations' or 'job'. Omit for all."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dft_set",
            "description": (
                "Change settings in DFT Workbench, exactly as the user "
                "would in its panel: the task (e.g. band structure, "
                "phonons, a relaxation), code, HPC profile, and any "
                "parameter setting. Only set values the user gave or "
                "named, never values of your own; if the user wants a "
                "change without saying the value, show the options from "
                "dft_settings and ask. If their goal fits several tasks "
                "without naming one, ask which. Keys are goldilocks-core's "
                "own (dft_settings lists them); null puts a setting back to "
                "automatic. All values are checked first: if any is wrong, "
                "none is set and the error says why. goldilocks-core then "
                "re-decides the other parameters, and the result is the new "
                "review (as dft_review returns it), shown to the user as a "
                "card."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "object",
                        "description": (
                            'Setting key to value, e.g. {"task": '
                            '"phonon"}, {"walltime_h": 24}, '
                            '{"smearing_type": "cold"}, '
                            '{"k_grid": [8, 8, 8]} or {"k_grid": null}.'
                        ),
                    },
                },
                "required": ["values"],
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
                "Ask the user before applying a remedy with dft_set."
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
    {
        "type": "function",
        "function": {
            "name": "dft_magnetic_orderings",
            "description": (
                "List the magnetic orderings goldilocks-core enumerates for "
                "the open structure (ferromagnetic and antiferromagnetic "
                "candidates), as DFT Workbench's Magnetic Orderings card "
                "shows them. Only for a spin-polarized calculation. Returns "
                "each candidate's label, formula and atom count, and, once "
                "the user has ranked them with the card's Run mMACE button, "
                "their energy per atom and which one is recommended. The "
                "chat doesn't rank them: to compare energies, tell the user "
                "to run mMACE in the card. To use a candidate the user "
                "chose, set its label as magnetic_ordering with dft_set."
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
    stub.__name__: stub
    for stub in (
        dft_open_structure,
        dft_settings,
        dft_set,
        dft_review,
        dft_download_bundle,
        dft_magnetic_orderings,
    )
}

CLIENT_EXECUTED: set[str] = set(TOOL_DISPATCH)

# Tools that set values the user stated (design doc 3.3's `relay`): their
# tool message keeps the id of the user message the turn answers, injected
# by `call_tool`, so a `human` value can be traced back to what the user
# said. Never a parameter -- the model could invent one.
ORIGIN_AUDITED: set[str] = {"dft_set"}

# No confirmation gate on any of these -- unlike MLIP Playground's real local
# compute, nothing here is consequential *to the user's machine* until they
# themselves click a Download button, which browsers already force to be a
# direct, unavoidable click (see app/src/dftWorkbench.ts's dispatchDftTool).
# A chat-level "Run it" card ahead of that would just be a second, redundant
# click gating nothing real -- 2026-09-25, removed after exactly that
# friction was flagged in real use.
CONFIRMATION_REQUIRED: set[str] = set()

CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {}
