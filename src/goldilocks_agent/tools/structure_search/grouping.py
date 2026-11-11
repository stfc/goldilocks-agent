"""Dedupe/rank search_by_formula's flat candidate list into distinct structures,
and optionally narrow the result to specific property categories.

Motivation (design conversation 2026-09-15): a formula search fans out across
four sources and can return dozens of raw entries, many of which are the same
structure indexed independently by more than one database, or genuinely
distinct polymorphs. Presenting that flat list makes the user click through
every entry to find anything useful -- grouping by (formula, spacegroup)
and ranking puts the lowest-energy structure first, its formation energy
compared only with the same database's others (functionals differ between
databases), and then one corroborated by more independent sources.

`filter_by_properties` backs the UI panel's "What are you looking for?" chips
(Electronic structure / Stability & energy / Magnetic / Elastic & mechanical /
Phonons & thermal / Optical) -- narrowing what a tool call returns to what the
user actually asked about, not just decoration on an unfiltered dump.
"""

from __future__ import annotations

from pymatgen.core import Composition

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

# Formation energies closer than this (eV/atom) count as the same lowest one,
# e.g. two entries of the same structure in one database.
_LOWEST_TOLERANCE = 1e-3

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


def _same_formula(formula: str) -> str:
    """One spelling per composition: the sources order elements differently
    (Materials Cloud and NOMAD give NaCl as "ClNa"), which split one structure
    into two groups. Falls back to the formula as given if it doesn't parse."""
    try:
        return Composition(formula).reduced_formula
    except (ValueError, KeyError):
        return formula


def _rank_energies(groups: list[CandidateGroup], entries: list[MatchEntry]) -> None:
    """Fill each group's energy_above_lowest and lowest_energy_in from
    formation energies, comparing a database only with itself: the lowest
    per (database, composition) over every entry in the search, grouped or
    not."""
    lowest: dict[tuple[str, str], float] = {}
    for entry in entries:
        if entry.formation_energy_per_atom is None:
            continue
        key = (entry.source, _same_formula(entry.formula))
        lowest[key] = min(
            lowest.get(key, entry.formation_energy_per_atom),
            entry.formation_energy_per_atom,
        )
    for group in groups:
        above: dict[str, float] = {}
        for entry in group.entries:
            if entry.formation_energy_per_atom is None:
                continue
            gap = (
                entry.formation_energy_per_atom - lowest[(entry.source, group.formula)]
            )
            above[entry.source] = min(above.get(entry.source, gap), gap)
        if above:
            group.energy_above_lowest = min(above.values())
            group.lowest_energy_in = sorted(
                source for source, gap in above.items() if gap <= _LOWEST_TOLERANCE
            )


def _sort_key(group: CandidateGroup) -> tuple[float, int, int]:
    # Lowest energy first, judged within one database (energy_above_lowest;
    # None sorts last -- unknown isn't evidence of instability); then more
    # independent sources; then more raw entries as a final tiebreak.
    energy = group.energy_above_lowest
    return (
        energy if energy is not None else float("inf"),
        -len(group.sources),
        -len(group.entries),
    )


def group_candidates(
    result: StructureMatchResult, max_groups: int = _DEFAULT_MAX_GROUPS
) -> GroupedSearchResult:
    """Dedupe `result.results` by (formula, spacegroup) and rank the groups;
    formulas compare by composition (`_same_formula`).

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
        key = (_same_formula(entry.formula), entry.spacegroup)
        groups_by_key.setdefault(key, []).append(entry)

    groups = [
        _build_group(formula, spacegroup, entries)
        for (formula, spacegroup), entries in groups_by_key.items()
    ]
    _rank_energies(groups, result.results)
    groups.sort(key=_sort_key)

    truncated_group_count = max(0, len(groups) - max_groups)
    return GroupedSearchResult(
        query_formula=result.query_formula or "",
        groups=groups[:max_groups],
        ungrouped=ungrouped,
        truncated_group_count=truncated_group_count,
        errors=result.errors,
        skipped=result.skipped,
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
