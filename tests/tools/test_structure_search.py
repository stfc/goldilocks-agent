"""Find in Databases: formula-search mode (design doc 十二 · implementation
plan 第 5 步 area, 2026-09-15 confirmed as the next piece to build).

Only formula mode is tested here -- file mode isn't implemented yet (not
because it needs core, see query.py's module docstring for the 2026-09-15
correction; it's just not built). Materials Cloud and NOMAD
are public OPTIMADE endpoints with no key, so these hit the real network
(AGENTS.md testing philosophy: verify real behavior, not mocks) and are
marked `integration` like the local-model tests. Materials Project needs
`MP_API_KEY` -- untested here since that's a per-developer secret, but
`search_by_formula` degrades that source to an `errors` entry rather than
raising, which is exercised regardless of whether the key is set.
"""

from __future__ import annotations

import asyncio

import pytest

from goldilocks_agent.tools.structure_search import (
    MatchEntry,
    StructureMatchResult,
    filter_by_properties,
    group_candidates,
    parse_query_formula,
    search_by_formula,
)


def test_parse_query_formula_reduces_and_rejects_garbage() -> None:
    assert parse_query_formula("Na2Cl2") == "NaCl"
    with pytest.raises(ValueError, match="Cannot parse formula"):
        parse_query_formula("not a formula!!!")


def _entry(source: str, spacegroup: str | None = "Fm-3m", **overrides) -> MatchEntry:
    defaults = {
        "formula": "NaCl",
        "spacegroup": spacegroup,
        "entry_id": f"{source}-id",
        "source": source,
        "url": f"https://example.com/{source}",
        "matched": False,
        "score": None,
    }
    return MatchEntry(**{**defaults, **overrides})


def test_group_candidates_merges_same_structure_across_sources() -> None:
    result = StructureMatchResult(
        results=[_entry("Materials Project"), _entry("NOMAD")],
        query_formula="NaCl",
        errors={},
    )

    grouped = group_candidates(result)

    assert len(grouped.groups) == 1
    group = grouped.groups[0]
    assert group.sources == ["Materials Project", "NOMAD"]
    assert len(group.entries) == 2
    assert grouped.ungrouped == []


def test_group_candidates_separates_entries_with_unknown_spacegroup() -> None:
    result = StructureMatchResult(
        results=[_entry("Materials Project"), _entry("NOMAD", spacegroup=None)],
        query_formula="NaCl",
        errors={},
    )

    grouped = group_candidates(result)

    assert len(grouped.groups) == 1
    assert len(grouped.ungrouped) == 1
    assert grouped.ungrouped[0].source == "NOMAD"


def test_group_candidates_prefers_materials_project_numbers() -> None:
    result = StructureMatchResult(
        results=[
            _entry("Materials Project", band_gap=5.0, band_gap_method="PBE"),
            _entry("JARVIS", band_gap=5.3, band_gap_method="OptB88vdW"),
        ],
        query_formula="NaCl",
        errors={},
    )

    grouped = group_candidates(result)

    group = grouped.groups[0]
    assert group.band_gap == 5.0
    assert group.band_gap_method == "PBE"
    assert group.property_sources["band_gap"] == "Materials Project"


def test_group_candidates_ranks_corroborated_groups_first() -> None:
    # Single-source "Ne1" group has the better (lower) energy_above_hull, but
    # the two-source "Fm-3m" group should still rank first -- corroboration
    # across independent databases outweighs one source's stability number.
    result = StructureMatchResult(
        results=[
            _entry("JARVIS", spacegroup="Ne1", energy_above_hull=0.0),
            _entry("Materials Project", spacegroup="Fm-3m", energy_above_hull=0.05),
            _entry("NOMAD", spacegroup="Fm-3m", energy_above_hull=None),
        ],
        query_formula="NaCl",
        errors={},
    )

    grouped = group_candidates(result)

    assert [g.spacegroup for g in grouped.groups] == ["Fm-3m", "Ne1"]


def test_group_candidates_truncates_beyond_max_groups() -> None:
    result = StructureMatchResult(
        results=[
            _entry("JARVIS", spacegroup=f"SG{i}", entry_id=f"jarvis-{i}")
            for i in range(5)
        ],
        query_formula="NaCl",
        errors={},
    )

    grouped = group_candidates(result, max_groups=3)

    assert len(grouped.groups) == 3
    assert grouped.truncated_group_count == 2


def test_filter_by_properties_clears_fields_outside_requested_categories() -> None:
    result = StructureMatchResult(
        results=[
            _entry(
                "Materials Project",
                band_gap=5.0,
                band_gap_method="PBE",
                energy_above_hull=0.0,
                total_magnetization=0.0,
            )
        ],
        query_formula="NaCl",
        errors={},
    )
    grouped = group_candidates(result)

    filtered = filter_by_properties(grouped, ["stability"])

    group = filtered.groups[0]
    assert group.energy_above_hull == 0.0  # requested category -- kept
    assert group.band_gap is None  # not requested -- cleared
    assert group.band_gap_method is None
    assert group.total_magnetization is None
    assert "band_gap" not in group.property_sources
    assert "energy_above_hull" in group.property_sources


def test_filter_by_properties_reports_phonons_as_unavailable() -> None:
    result = StructureMatchResult(
        results=[_entry("Materials Project")], query_formula="NaCl", errors={}
    )
    grouped = group_candidates(result)

    filtered = filter_by_properties(grouped, ["phonons", "electronic"])

    assert filtered.unavailable_properties == ["phonons"]


def test_filter_by_properties_no_categories_is_a_no_op() -> None:
    result = StructureMatchResult(
        results=[_entry("Materials Project", band_gap=5.0)],
        query_formula="NaCl",
        errors={},
    )
    grouped = group_candidates(result)

    assert filter_by_properties(grouped, None) == grouped
    assert filter_by_properties(grouped, []) == grouped


@pytest.mark.integration
def test_search_by_formula_hits_real_public_databases() -> None:
    """NaCl is guaranteed to exist in Materials Cloud/NOMAD/JARVIS -- if this
    comes back empty across the board, the query plumbing is broken, not
    just "no data for this formula"."""
    result = asyncio.run(search_by_formula("NaCl"))

    assert result.query_formula == "NaCl"
    # Materials Project errors without MP_API_KEY, JARVIS errors until its
    # cache is downloaded (`poe fetch-jarvis-cache`) -- both expected and
    # handled degradations in a fresh dev environment, not test failures.
    # Materials Cloud/NOMAD have no such gate; if they errored too, that's
    # the real network/plumbing failure this test exists to catch.
    assert set(result.errors) <= {"Materials Project", "JARVIS"}
    assert len(result.results) > 0
    assert all(r.formula for r in result.results)
    assert {r.source for r in result.results} <= {
        "Materials Project",
        "Materials Cloud",
        "NOMAD",
        "JARVIS",
    }

    grouped = group_candidates(result, max_groups=len(result.results))
    # No truncation at max_groups=len(results) -- every raw entry must land
    # in exactly one place, grouped or ungrouped, grouping must never drop
    # or duplicate a hit.
    assert grouped.truncated_group_count == 0
    regrouped_count = sum(len(g.entries) for g in grouped.groups) + len(
        grouped.ungrouped
    )
    assert regrouped_count == len(result.results)
    # Rocksalt NaCl (Fm-3m) is corroborated by more than one of these four
    # sources -- if grouping can't find that, the dedup key is wrong.
    assert any(g.spacegroup == "Fm-3m" and len(g.sources) >= 2 for g in grouped.groups)
