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
