"""Result models for the goldilocks-core CLI operations this Tool wraps
(inspect, explain, run). Field names verified 2026-09-15 against real
`goldilocks {inspect,explain,run} --json` output on the bundled `Si.cif`
example (`4-goldilocks-core`'s own checkout) -- not guessed from docs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ResolvedField(BaseModel):
    """One entry in explain/run's `records` dict -- one analysis fact or
    advisor decision. Exactly one of `value`/`reason`/`blocked_by` is
    populated, matching `status`."""

    status: Literal["resolved", "unavailable", "blocked"]
    value: Any = None
    source: Literal["human", "ml", "llm", "heuristic"] | None = None
    reason: str | None = None
    blocked_by: str | None = None
    field_sources: dict[str, str] | None = None


class InspectResult(BaseModel):
    canonical_cif: str
    schema_version: int
    source: dict[str, Any]
    structure: dict[str, Any]


class ExplainResult(BaseModel):
    records: dict[str, ResolvedField]
    warnings: list[dict[str, Any]]


class RunResult(BaseModel):
    """`goldilocks run -o <dir> --json` -- verified live 2026-09-15 this
    returns `{files: [...], kind, path}`, NOT `{files, records, warnings}`
    (that richer shape is only "memory-only preview" mode, i.e. `run`
    *without* `-o`). No `records`/`warnings` here on purpose: the panel's
    Explain tab reads those off the separate `explain()` call it already
    makes (same underlying resolution, no file-write side effect), rather
    than paying for a second, redundant CLI invocation here."""

    # janus-api-style "write then read back" -- content keyed by relative
    # filename (scf.in, submit.sh, goldilocks.json, README.md,
    # CITATIONS.md, pseudo/<element>.upf). Unlike an earlier pass, pseudo
    # files are *not* excluded any more -- UPF is plain text (XML-ish), not
    # binary, and the Inputs tab now lists it alongside the input/
    # submission-script files (2026-09-15, second pass).
    files: dict[str, str]

    def model_dump_for_llm(self) -> dict:
        """The panel's REST path (`/api/dft/run`) always wants every file,
        including `pseudo/*.upf` (a few hundred KB of plain text) -- but the
        LLM tool-calling path's output gets `json.dumps()`'d into a `tool`
        message that persists in the checkpointer's history forever, so
        shipping the pseudopotential text into every subsequent call on
        that thread would be real, growing waste (same reasoning
        mlip_playground's models document for their own heavy fields).
        `dft_workbench/tool.py`'s `dft_generate` schema tells the model the
        real bundle -- pseudopotential included -- is one click away in the
        panel's Inputs tab."""
        return {
            "files": {
                name: content
                for name, content in self.files.items()
                if not name.startswith("pseudo/")
            }
        }


class SettingSpec(BaseModel):
    """One entry from `capabilities()`'s `settings[]` -- same shape whether
    reached via HTTP/MCP's `/capabilities` or (since goldilocks-core#62
    landed, 2026-09-15) `goldilocks capabilities --json`/`goldilocks
    settings --json` (both thin CLI entry points onto the same function)."""

    key: str
    group: str
    type: str
    scope: Literal["system", "per_step"]
    description: str
    unit: str | None = None
    default: Any = None
    enum: list[str] | None = None
    enum_from: str | None = None
    ml_target: str | None = None
    codes: list[str] | None = None
    tasks: list[str] | None = None
    programs: list[str] | None = None
    approaches: list[str] | None = None


class CodeInfo(BaseModel):
    id: str
    name: str
    tasks: list[str]


class TaskInfo(BaseModel):
    id: str
    name: str
    description: str
    codes: list[str]
    executables: list[str]
    step_count: int


class PseudoTableInfo(BaseModel):
    id: str
    provider: str
    functional: str
    accuracy: str
    relativistic: str
    default: bool
    elements: list[str]
    citation: str
    licence: str
    version: str


class HpcProfileInfo(BaseModel):
    id: str
    name: str
    scheduler: str
    partitions: list[str]


class FactInfo(BaseModel):
    """One entry from `capabilities()`'s `facts[]` -- what `explain`'s
    `records` can resolve about the *structure itself* (is_metal,
    is_magnetic, ...), as opposed to `settings[]`'s calculation
    parameters."""

    key: str
    description: str
    type: str
    values: list[str] | None = None
    approaches: list[str]
    overridable: bool
    ml_target: str | None = None


class WarningCatalogEntry(BaseModel):
    code: str
    category: str
    level: str
    message: str


class CapabilitiesResult(BaseModel):
    """`goldilocks capabilities --json` -- goldilocks-core#62, filed
    2026-09-15 when the CLI had no equivalent of HTTP/MCP's one-call
    `/capabilities`, closed the same day. Replaces the earlier
    `goldilocks settings --json`-only path (`SettingSpec` still exists,
    now as this payload's `settings[]` field, not its own top-level
    fetch) -- codes/tasks/pseudopotential_tables/hpc_profiles are real
    dropdown data now, not the panel's own hardcoded placeholders."""

    core_version: str
    vocabulary_version: str
    codes: list[CodeInfo]
    tasks: list[TaskInfo]
    facts: list[FactInfo]
    hpc_profiles: list[HpcProfileInfo]
    models: list[Any] = []
    pseudopotential_tables: list[PseudoTableInfo]
    settings: list[SettingSpec]
    sources: list[str]
    warnings: list[WarningCatalogEntry]
