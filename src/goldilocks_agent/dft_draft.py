"""A chat's DFT Workbench draft, kept on the agent server (#142).

The draft is what has been set for a chat's calculation -- in the panel, or
through the chat: the open structure, the code, task and HPC profile, and the
overrides, as goldilocks-workbench's `Workspace` holds them (its
`structureInput` and `CalculationDraft`). Everything else the panel shows --
the review, the preview bundle, magnetic orderings -- goldilocks-core derives
from it, so it isn't kept. One draft per chat, in `store.py`'s `dft_drafts`
table, with a revision every write must name (design doc 3.3's
`draft_revision`): a writer working from an older draft is refused instead of
overwriting a newer one.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The panel refuses larger structure files (goldilocks-workbench's upload
# check), so no draft needs more.
MAX_STRUCTURE_CHARS = 5 * 1024 * 1024


class StructureInput(BaseModel):
    """A structure as goldilocks-core's /inspect takes it."""

    model_config = ConfigDict(extra="forbid")

    structure_content: str = Field(max_length=MAX_STRUCTURE_CHARS)
    structure_name: str
    structure_format: str | None = None


class DftDraft(BaseModel):
    """What a chat's DFT Workbench has set. `vocabulary` is the
    goldilocks-core vocabulary the overrides were set under: override keys
    and values change between vocabularies, so a browser restores them only
    under the same one."""

    model_config = ConfigDict(extra="forbid")

    vocabulary: str | None = None
    structure: StructureInput | None = None
    code: str
    task: str
    hpc: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class DftDraftUpdate(BaseModel):
    """A new draft, and the revision of the one it replaces (0 for none)."""

    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=0)
    draft: DftDraft


# The package's own draft rules (goldilocks-workbench's `Workspace`), for the
# chat's tools on the server: the panel's Workspace applies the same ones in
# the browser. Each reads goldilocks-core's `/capabilities` rather than a
# copied list, so a setting core adds is handled with no change here.


def default_draft(capabilities: dict) -> DftDraft:
    """A new chat's draft: core's first code and task, no HPC profile and
    no overrides."""
    codes = capabilities.get("codes") or []
    tasks = capabilities.get("tasks") or []
    return DftDraft(
        vocabulary=capabilities.get("vocabulary_version"),
        code=codes[0]["id"] if codes else "quantum_espresso",
        task=tasks[0]["id"] if tasks else "scf_single_point",
        hpc=None,
        overrides={},
    )


def open_structure(
    draft: DftDraft | None, structure: StructureInput, capabilities: dict
) -> tuple[DftDraft, list[str]]:
    """The draft with `structure` open, and the override keys cleared. The
    code, task, HPC profile and method settings carry over; the overrides
    that described the structure it replaces don't -- structure-specific
    settings and facts. A draft from another vocabulary starts over."""
    vocabulary = capabilities.get("vocabulary_version")
    if draft is None or draft.vocabulary != vocabulary:
        draft = default_draft(capabilities)
    carried = {
        setting["key"]
        for setting in capabilities.get("settings", [])
        if not setting.get("structure_specific")
    }
    kept = {key: value for key, value in draft.overrides.items() if key in carried}
    cleared = [key for key in draft.overrides if key not in carried]
    opened = draft.model_copy(
        update={"structure": structure, "overrides": kept, "vocabulary": vocabulary}
    )
    return opened, cleared


def structure_request(structure: StructureInput) -> dict:
    """The structure fields core's `/inspect` and `/magnetic-orderings` take."""
    return structure.model_dump(exclude_none=True)


def compute_request(draft: DftDraft, capabilities: dict) -> dict:
    """The `/explain` or `/run` body for a draft with a structure open. An
    override that is a setting goes only when the task reads it; facts
    always go. `fetch_missing` is left to core's default: core 0.3.3 takes
    it false, and its removal (goldilocks-core fork #299) then needs no
    change here."""
    if draft.structure is None:
        raise ValueError("compute_request needs a draft with a structure open")
    settings = {setting["key"]: setting for setting in capabilities.get("settings", [])}
    overrides = {
        key: value
        for key, value in draft.overrides.items()
        if key not in settings or draft.task in settings[key].get("tasks", [])
    }
    return {
        **structure_request(draft.structure),
        "code": draft.code,
        "task": draft.task,
        "hpc": draft.hpc,
        "overrides": overrides,
    }


def structure_key(structure: StructureInput) -> str:
    """Identifies an open structure, e.g. so a magnetic-ordering ranking is
    only used for the structure it was run on."""
    digest = hashlib.sha256()
    for part in (
        structure.structure_name,
        structure.structure_format or "",
        structure.structure_content,
    ):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()
