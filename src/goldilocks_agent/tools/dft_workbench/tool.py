"""The chat's DFT Workbench tools, run on the agent server (#142).

The tools mirror the panel's own actions, grouped by intent (design doc
12.5): `dft_open_structure` opens a structure, `dft_settings` reads every
setting the panel shows, `dft_set` changes any of them (the Calculation
form's code/task/HPC profile and every override control),
`dft_review`/`dft_download_bundle` review and generate, and
`dft_magnetic_orderings` lists magnetic orderings (the chat doesn't rank:
Junwen, 2026-11-16).

Each works on the chat's own draft, kept on the server (`dft_drafts`,
`dft_draft.py`), and calls goldilocks-core over HTTP (`core_api.py`). A
change is checked with core before it is committed, under the revision it
was made from; the browser hears of it as a `dft_draft` event and replays
it into the chat's panel. Setting keys and ids are free strings, not enums:
the valid ones come from core's `/capabilities`, and an unknown one is
refused with the closest valid keys (design doc 3.3's "an error that
teaches").

Until 2026-11-16 these ran in the browser, on the panel's own Workspace
(#137's client-executed tools). That needed an open page, couldn't be tested
headlessly, raced the panel's state machine and shared one draft across
chats (#142), so they moved here; the panel stays a view of the draft.

`dft_set` is design doc 3.3's `relay`: values the user stated, which core
stamps `human`. The id of the user message they came from is injected by
`graph.py`'s `call_tool` (`ORIGIN_AUDITED`), never passed by the model.
Values the model chooses itself (`suggest`, the `llm` tier) wait on
goldilocks-core#258 (stfc/goldilocks-agent#67).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from goldilocks_agent import store
from goldilocks_agent.core_api import CoreFailure
from goldilocks_agent.dft_draft import (
    DftDraft,
    compute_request,
    default_draft,
    open_structure,
    structure_request,
)
from goldilocks_agent.tools.context import ToolContext
from goldilocks_agent.tools.dft_workbench import bundles
from goldilocks_agent.tools.dft_workbench.review import (
    calculation_of,
    failure_text,
    findings_text,
    review_for_model,
    structure_summary,
)
from goldilocks_agent.tools.dft_workbench.settings import (
    apply_changes,
    describe_changes,
    setting_changes,
    settings_catalogue,
)
from goldilocks_agent.tools.dft_workbench.structures import (
    chat_structures,
    missing_structure_text,
    structure_input,
)

NO_STRUCTURE = (
    "No structure has been opened in DFT Workbench yet -- call dft_open_structure "
    "first."
)
UNSETTLED = (
    "DFT Workbench's settings kept changing while this ran -- call the tool again."
)

# A name, never the file's text: re-typing a structure into a tool call is
# slow, and smaller models silently truncate or alter it (#73). The server
# looks the name up among this chat's structure files (`structures.py`).
_FILE_NAME_PARAM = {
    "type": "string",
    "description": (
        "The exact name of a structure file in this chat: a file the user "
        "attached (the NAME in '[Attached file: NAME]') or the filename a "
        "get_structure call returned. DFT Workbench reads the file itself, "
        "so never pass the file's content."
    ),
}


def _reports_core_failures(
    tool: Callable[..., Awaitable[dict]],
) -> Callable[..., Awaitable[dict]]:
    """A core failure becomes a refusal the model can explain: its blocking
    findings with their remedies, else core's kind and message."""

    @functools.wraps(tool)
    async def wrapped(ctx: ToolContext, **args: Any) -> dict:
        try:
            return await tool(ctx, **args)
        except CoreFailure as failure:
            raise ValueError(failure_text(failure)) from failure

    return wrapped


async def _capabilities(ctx: ToolContext) -> dict:
    """Core's capabilities, once core is up; a first start's progress goes
    to the chat's status line."""

    def waiting(line: str | None) -> None:
        label = "Starting goldilocks-core..." + (f" {line}" if line else "")
        ctx.emit({"type": "tool_status", "tool": "dft_workbench", "label": label})

    await ctx.core.ready(on_wait=waiting)
    return await ctx.core.capabilities()


async def _load(ctx: ToolContext) -> tuple[int, DftDraft | None]:
    """The chat's draft and its revision (0 and None before one exists)."""
    row = await store.get_dft_draft(ctx.store, ctx.thread_id)
    if row is None:
        return 0, None
    revision = row.pop("revision")
    return revision, DftDraft(**row)


async def _commit(
    ctx: ToolContext, change: Callable[[DftDraft | None], DftDraft]
) -> DftDraft:
    """Saves `change` applied to the chat's draft, under the revision it was
    made from, and tells the browser. A panel edit saved meanwhile wins the
    race; the change is then made once more on top of it."""
    for _attempt in range(2):
        revision, current = await _load(ctx)
        changed = change(current)
        saved, row = await store.put_dft_draft(
            ctx.store, ctx.thread_id, changed.model_dump(), revision
        )
        if saved:
            ctx.emit({"type": "dft_draft", "draft": row})
            return changed
    raise ValueError(UNSETTLED)


def _worth_retrying(failure: CoreFailure) -> bool:
    """Whether the same request can turn out differently: core unreachable,
    failing on its side, or missing an asset. A 4xx refusal is the draft's
    own."""
    return failure.kind in ("network_error", "core_unavailable") or (
        failure.status is not None and (failure.status >= 500 or failure.status == 424)
    )


@_reports_core_failures
async def dft_open_structure(ctx: ToolContext, *, file_name: str) -> dict:
    capabilities = await _capabilities(ctx)
    structures = chat_structures(ctx.messages)
    found = next((s for s in structures if s.name == file_name), None)
    _, current = await _load(ctx)
    if found is not None:
        structure = structure_input(found)
    elif (
        current is not None
        and current.structure is not None
        and (current.structure.structure_name == file_name)
    ):
        # A file the user opened in the panel, never sent in the chat.
        structure = current.structure
    else:
        raise ValueError(missing_structure_text(file_name, structures))
    inspection = await ctx.core.inspect(structure_request(structure))
    cleared: list[str] = []

    def opened(draft: DftDraft | None) -> DftDraft:
        nonlocal cleared
        result, cleared = open_structure(draft, structure, capabilities)
        return result

    draft = await _commit(ctx, opened)
    return {
        "structure": structure_summary(inspection),
        "calculation": calculation_of(draft),
        # Structure-specific overrides the previous structure had set, which
        # opening this one cleared; code, task, HPC profile and the method
        # settings carry over.
        "cleared_overrides": cleared,
        "tasks_available": [task["id"] for task in capabilities["tasks"]],
    }


@_reports_core_failures
async def dft_settings(ctx: ToolContext, *, group: str | None = None) -> dict:
    capabilities = await _capabilities(ctx)
    _, draft = await _load(ctx)
    draft = draft or default_draft(capabilities)
    inspection = explain = None
    if draft.structure is not None:
        inspection = await ctx.core.inspect(structure_request(draft.structure))
        # The current values come from a review of the draft; without one
        # (core refusing it) the catalogue still lists what can be set.
        try:
            explain = await ctx.core.explain(compute_request(draft, capabilities))
        except CoreFailure:
            explain = None
    return settings_catalogue(capabilities, draft, inspection, explain, group)


@_reports_core_failures
async def dft_set(ctx: ToolContext, *, values: dict) -> dict:
    capabilities = await _capabilities(ctx)
    _, draft = await _load(ctx)
    draft = draft or default_draft(capabilities)
    inspection = (
        None
        if draft.structure is None
        else await ctx.core.inspect(structure_request(draft.structure))
    )
    changes = setting_changes(values, capabilities, draft, inspection)
    candidate = apply_changes(draft, changes)
    changed = describe_changes(changes)

    def commit() -> Awaitable[DftDraft]:
        return _commit(ctx, lambda current: apply_changes(current or draft, changes))

    if candidate.structure is None:
        if changes:
            candidate = await commit()
        return {"changed": changed, "calculation": calculation_of(candidate)}
    try:
        explain = await ctx.core.explain(compute_request(candidate, capabilities))
    except CoreFailure as failure:
        if not _worth_retrying(failure):
            # Core refuses the draft itself: nothing is committed, so the
            # panel isn't left with a draft core won't take.
            raise ValueError(
                f"Nothing was set, goldilocks-core refused it: {failure_text(failure)}"
            ) from failure
        if changes:
            await commit()
        raise ValueError(
            "The settings were changed, but goldilocks-core couldn't review them: "
            f"{failure_text(failure)}"
        ) from failure
    if changes:
        candidate = await commit()
    return {"changed": changed, **review_for_model(candidate, explain)}


@_reports_core_failures
async def dft_review(ctx: ToolContext) -> dict:
    capabilities = await _capabilities(ctx)
    _, draft = await _load(ctx)
    if draft is None or draft.structure is None:
        raise ValueError(NO_STRUCTURE)
    explain = await ctx.core.explain(compute_request(draft, capabilities))
    return review_for_model(draft, explain)


@_reports_core_failures
async def dft_download_bundle(ctx: ToolContext) -> dict:
    capabilities = await _capabilities(ctx)
    _, draft = await _load(ctx)
    if draft is None or draft.structure is None:
        raise ValueError(NO_STRUCTURE)
    request = compute_request(draft, capabilities)
    explain = await ctx.core.explain(request)
    if explain.get("blocking"):
        raise ValueError(
            "Input files can't be generated for the current settings: "
            f"{findings_text(explain['blocking'])} Ask the user before changing "
            "any setting."
        )
    archive = await ctx.core.run_archive(request)
    bundles.save_bundle(ctx.thread_id, archive.filename, archive.content)
    return {
        "filename": archive.filename,
        "download_url": bundles.download_url(ctx.thread_id, archive.filename),
        "note": (
            "Ready -- a Download button for it appears in the chat right here "
            "(browsers won't let a chat action save it automatically, so the "
            "user clicks it)."
        ),
    }


@_reports_core_failures
async def dft_magnetic_orderings(ctx: ToolContext) -> dict:
    capabilities = await _capabilities(ctx)
    _, draft = await _load(ctx)
    if draft is None or draft.structure is None:
        raise ValueError(NO_STRUCTURE)
    explain = await ctx.core.explain(compute_request(draft, capabilities))
    # The panel's Magnetic Orderings card shows only for a spin-polarized
    # calculation.
    magnetic = explain["records"].get("magnetic") or {}
    value = magnetic.get("value") if magnetic.get("status") == "resolved" else None
    if not isinstance(value, dict) or value.get("spin_polarized") is not True:
        raise ValueError(
            "This calculation isn't spin-polarized, so DFT Workbench has no "
            "magnetic orderings for it."
        )
    listing = await ctx.core.magnetic_orderings(structure_request(draft.structure))
    return {
        "ranked": listing.get("ranked", False),
        # The candidates' override values stay with core and the panel.
        "candidates": [
            {
                key: candidate.get(key)
                for key in (
                    "label",
                    "formula",
                    "natoms",
                    "energy_per_atom_ev",
                    "status",
                    "is_recommended",
                )
            }
            for candidate in listing.get("candidates", [])
        ],
        "warnings": [w.get("message") for w in listing.get("warnings", [])],
    }


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
                "click a 'confirm' button first. A Download button for the "
                "bundle appears right here in the chat -- not on disk "
                "directly: tell the user to click it to save the file, "
                "browsers don't allow a chat action to save one on its own."
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
                "each candidate's label, formula and atom count. The chat "
                "doesn't rank them: to compare their energies, tell the user "
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
    tool.__name__: tool
    for tool in (
        dft_open_structure,
        dft_settings,
        dft_set,
        dft_review,
        dft_download_bundle,
        dft_magnetic_orderings,
    )
}

# Every one works on the chat itself: `call_tool` passes a ToolContext.
CONTEXT_TOOLS: set[str] = set(TOOL_DISPATCH)

# Tools that set values the user stated (design doc 3.3's `relay`): their
# tool message keeps the id of the user message the turn answers, injected
# by `call_tool`, so a `human` value can be traced back to what the user
# said. Never a parameter -- the model could invent one.
ORIGIN_AUDITED: set[str] = {"dft_set"}

# No confirmation gate on any of these -- unlike MLIP Playground's real local
# compute, nothing here is consequential *to the user's machine* until they
# themselves click a Download button, which browsers already force to be a
# direct, unavoidable click. A chat-level "Run it" card ahead of that would
# just be a second, redundant click gating nothing real -- 2026-09-25,
# removed after exactly that friction was flagged in real use.
CONFIRMATION_REQUIRED: set[str] = set()

CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {}
