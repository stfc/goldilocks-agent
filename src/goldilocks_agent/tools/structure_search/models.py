"""Pydantic models shared by structure_search's query, fetch, and grouping paths."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Every enrichment field below maps to one of the UI panel's six property
# filter chips (grouping._PROPERTY_FIELDS is the canonical mapping) except
# `density`, which isn't gated by any chip. Real per-source availability
# verified 2026-09-15 against live Materials Project/JARVIS calls and a real
# NOMAD OPTIMADE response (AGENTS.md: verified, not assumed) -- coverage is
# genuinely uneven across sources:
# - Materials Project: populates every field below in the same
#   summary.search() call that already runs today (widening `fields=[...]`
#   is free) -- confirmed against a real mp-22862 (NaCl) call.
# - JARVIS: populates every field from the raw cached entry (zero network
#   cost) -- confirmed against real cached NaCl entries.
# - NOMAD: `band_gap`/`band_gap_method` are best-effort -- OPTIMADE metadata
#   often lists a property as *available* without exposing its value. The
#   rest are left None rather than guessed.
# - Materials Cloud: OPTIMADE's standard fields are structural only -- never
#   populated here, not a bug.
# No source below exposes real phonon data at this tier (Materials Project's
# `phonon_IDs` field is just record ids, not values) -- there is
# deliberately no `phonons` field; `grouping.filter_by_properties` reports
# that category as unavailable instead of returning an always-None field.


class MatchEntry(BaseModel):
    formula: str
    spacegroup: str | None
    entry_id: str | None
    source: str
    url: str
    matched: bool
    score: float | None
    band_gap: float | None = None  # eV
    # e.g. "PBE", "OptB88vdW" -- methods differ enough across sources that the
    # number is misleading without this.
    band_gap_method: str | None = None
    formation_energy_per_atom: float | None = None  # eV/atom
    energy_above_hull: float | None = None  # eV/atom; 0 = thermodynamic ground state
    density: float | None = None  # g/cm^3
    total_magnetization: float | None = None  # Bohr magnetons per formula unit
    bulk_modulus: float | None = None  # GPa, Voigt-Reuss-Hill average where applicable
    shear_modulus: float | None = None  # GPa, Voigt-Reuss-Hill average where applicable
    refractive_index: float | None = None  # static, dimensionless
    dielectric_constant: float | None = None  # static total (electronic + ionic)


class StructureMatchResult(BaseModel):
    results: list[MatchEntry]
    query_formula: str | None
    errors: dict[str, str]


class CandidateGroup(BaseModel):
    """One distinct (formula, spacegroup) structure, deduped across sources.

    `entries` keeps every source's raw MatchEntry (for their individual urls);
    the top-level enrichment fields are the single best available number for
    this structure, picked by `grouping.group_candidates()` from whichever
    entry in the group reported it. `property_sources` maps each populated
    field name to which source it came from, so a narration can attribute
    the number instead of presenting it as if all sources agreed.
    """

    formula: str
    spacegroup: str | None
    sources: list[str]
    entries: list[MatchEntry]
    band_gap: float | None = None
    band_gap_method: str | None = None
    formation_energy_per_atom: float | None = None
    energy_above_hull: float | None = None
    density: float | None = None
    total_magnetization: float | None = None
    bulk_modulus: float | None = None
    shear_modulus: float | None = None
    refractive_index: float | None = None
    dielectric_constant: float | None = None
    property_sources: dict[str, str] = Field(default_factory=dict)


class GroupedSearchResult(BaseModel):
    """`search_by_formula`'s flat candidate list, deduped, ranked, and
    optionally narrowed to the property categories the caller asked about
    (`grouping.filter_by_properties`).

    `groups` is capped (see `grouping.group_candidates`'s `max_groups`) so a
    formula with many distinct polymorphs doesn't dump all of them into the
    LLM's context -- `truncated_group_count` says how many more exist.
    `unavailable_properties` lists any requested category (e.g. "phonons")
    that none of the four sources can supply at all, so the caller can say
    so instead of the omission reading as "found nothing."
    """

    query_formula: str
    groups: list[CandidateGroup]
    ungrouped: list[MatchEntry]  # spacegroup unknown -- can't confidently dedupe these
    truncated_group_count: int
    unavailable_properties: list[str] = Field(default_factory=list)
    errors: dict[str, str]


class FetchedStructure(BaseModel):
    filename: str
    content: str
    format: str = "cif"
