"""Dedupe/rank search_by_formula's flat candidate list into distinct structures,
and optionally narrow the result to specific property categories.

Motivation (design conversation 2026-09-15): a formula search fans out across
four sources and can return dozens of raw entries, many of which are the same
structure indexed independently by more than one database, or genuinely
distinct polymorphs. Presenting that flat list makes the user click through
every entry to find anything useful -- grouping by (formula, spacegroup)
surfaces a structure corroborated by multiple independent sources, or one
that's the thermodynamic ground state, before an isolated single-source hit.

`filter_by_properties` backs the UI panel's "What are you looking for?" chips
(Electronic structure / Stability & energy / Magnetic / Elastic & mechanical /
Phonons & thermal / Optical) -- narrowing what a tool call returns to what the
user actually asked about, not just decoration on an unfiltered dump.
"""

from __future__ import annotations

from goldilocks_agent.tools.structure_search.models import (
    CandidateGroup,
    GroupedSearchResult,
    MatchEntry,
    StructureMatchResult,
)

# Materials Project is the most internally consistent of the four (one
# methodology, PBE/GGA+U, across its whole database) -- when a group has an
# MP entry, prefer its numbers over a source that might use a different
# method for this particular material.
_SOURCE_PRIORITY = ["Materials Project", "JARVIS", "NOMAD", "Materials Cloud"]

_DEFAULT_MAX_GROUPS = 8

# Every field a group can carry, keyed by which of the UI panel's six filter
# chips it belongs to (models.py's module docstring has the per-source
# availability notes). `phonons` maps to no field on purpose -- none of the
# four sources expose real phonon data at this tier, so any group asked to
# report it will legitimately have nothing, not a lookup miss.
_PROPERTY_FIELDS: dict[str, list[str]] = {
    "electronic": ["band_gap"],
    "stability": ["formation_energy_per_atom", "energy_above_hull"],
    "magnetic": ["total_magnetization"],
    "elastic": ["bulk_modulus", "shear_modulus"],
    "optical": ["refractive_index", "dielectric_constant"],
    "phonons": [],
}
_GATED_FIELDS = {field for fields in _PROPERTY_FIELDS.values() for field in fields}
# Not one of the UI's six chips -- cheap, general context, never filtered out.
_ALWAYS_KEPT_FIELDS = {"density"}


def _best_entry(entries: list[MatchEntry], field: str) -> MatchEntry | None:
    """First entry with a non-None `field`, scanning sources in priority order."""
    by_source = {e.source: e for e in entries}
    for source in _SOURCE_PRIORITY:
        entry = by_source.get(source)
        if entry is not None and getattr(entry, field) is not None:
            return entry
    return None


def _build_group(
    formula: str, spacegroup: str, entries: list[MatchEntry]
) -> CandidateGroup:
    values: dict[str, float | None] = {}
    sources: dict[str, str] = {}
    band_gap_method = None
    for field in _GATED_FIELDS | _ALWAYS_KEPT_FIELDS:
        entry = _best_entry(entries, field)
        values[field] = getattr(entry, field) if entry else None
        if entry is not None:
            sources[field] = entry.source
            if field == "band_gap":
                band_gap_method = entry.band_gap_method

    return CandidateGroup(
        formula=formula,
        spacegroup=spacegroup,
        sources=sorted({e.source for e in entries}),
        entries=entries,
        band_gap_method=band_gap_method,
        property_sources=sources,
        **values,
    )


def _sort_key(group: CandidateGroup) -> tuple[int, float, int]:
    # More independent sources first; then closer to the thermodynamic ground
    # state (None sorts last, not first -- an unknown stability is not
    # evidence of instability); then more raw entries as a final tiebreak.
    hull = group.energy_above_hull
    return (
        -len(group.sources),
        hull if hull is not None else float("inf"),
        -len(group.entries),
    )


def group_candidates(
    result: StructureMatchResult, max_groups: int = _DEFAULT_MAX_GROUPS
) -> GroupedSearchResult:
    """Dedupe `result.results` by (formula, spacegroup) and rank the groups.

    Entries with no spacegroup (structure couldn't be resolved -- happens for
    some Materials Cloud/NOMAD hits) go to `ungrouped` instead of being
    guessed into a group.
    """
    groups_by_key: dict[tuple[str, str], list[MatchEntry]] = {}
    ungrouped: list[MatchEntry] = []

    for entry in result.results:
        if not entry.spacegroup:
            ungrouped.append(entry)
            continue
        groups_by_key.setdefault((entry.formula, entry.spacegroup), []).append(entry)

    groups = [
        _build_group(formula, spacegroup, entries)
        for (formula, spacegroup), entries in groups_by_key.items()
    ]
    groups.sort(key=_sort_key)

    truncated_group_count = max(0, len(groups) - max_groups)
    return GroupedSearchResult(
        query_formula=result.query_formula or "",
        groups=groups[:max_groups],
        ungrouped=ungrouped,
        truncated_group_count=truncated_group_count,
        errors=result.errors,
    )


def filter_by_properties(
    result: GroupedSearchResult, categories: list[str] | None
) -> GroupedSearchResult:
    """Narrow a GroupedSearchResult to the requested property categories.

    `categories=None` (or empty) returns `result` unchanged -- that's the
    default when nothing in particular was asked about. Fields outside the
    requested categories are cleared to None rather than the group being
    dropped -- the structure itself, and whether other sources corroborate
    it, is still relevant regardless of which property the user asked about.
    Unknown category ids are silently ignored rather than erroring, since
    the LLM populates this from free-form intent, not a validated form.
    """
    if not categories:
        return result

    keep = set(_ALWAYS_KEPT_FIELDS)
    unavailable = []
    for category in categories:
        fields = _PROPERTY_FIELDS.get(category)
        if fields is None:
            continue
        if not fields:
            unavailable.append(category)
        keep.update(fields)

    def _narrow(group: CandidateGroup) -> CandidateGroup:
        cleared: dict[str, float | str | None] = {
            field: None for field in _GATED_FIELDS if field not in keep
        }
        if "band_gap" in cleared:
            cleared["band_gap_method"] = None
        kept_sources = {
            field: source
            for field, source in group.property_sources.items()
            if field in keep
        }
        return group.model_copy(update={**cleared, "property_sources": kept_sources})

    return result.model_copy(
        update={
            "groups": [_narrow(g) for g in result.groups],
            "unavailable_properties": sorted(set(unavailable)),
        }
    )
