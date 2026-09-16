"""LLM-facing tool wrappers for structure_search.

The schema lives here, next to the functions it describes, so it can't drift
into a second, hand-copied text that goes stale (design doc: MCP tools "自带
描述与schema"; the same principle applies to the agent's own tools). `graph.py`
only ever imports the aggregated registry from `goldilocks_agent.tools`, never
reaches into a specific Tool's internals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.structure_search.grouping import (
    filter_by_properties,
    group_candidates,
)
from goldilocks_agent.tools.structure_search.models import (
    FetchedStructure,
    GroupedSearchResult,
)
from goldilocks_agent.tools.structure_search.query import (
    fetch_structure,
    search_by_formula,
)


async def find_in_databases(
    formula: str, properties: list[str] | None = None
) -> GroupedSearchResult:
    result = await search_by_formula(formula)
    grouped = group_candidates(result)
    return filter_by_properties(grouped, properties)


async def get_structure(source: str, entry_id: str) -> FetchedStructure:
    return await fetch_structure(source, entry_id)


TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "find_in_databases",
            "description": (
                "Search Materials Project, Materials Cloud, NOMAD, and JARVIS "
                "for existing computed structures matching a chemical formula. "
                "Use this when the user asks whether a material already has "
                "published/computed data, or wants a starting structure for a "
                "calculation -- as a pre-research step before generating new "
                "DFT inputs from scratch. Do not call this just because a "
                "formula was mentioned in passing; only when finding existing "
                "data is the actual intent. Results are deduped by structure "
                "(formula + space group) and ranked: structures corroborated "
                "by more than one independent database, or closer to the "
                "thermodynamic ground state (lower energy_above_hull), come "
                "first. A None field means that source didn't report the "
                "value, not that the value is zero -- say so rather than "
                "silently omitting it. Different sources use different "
                "methods for band_gap (see band_gap_method) -- note the "
                "method when comparing numbers across sources. If "
                "unavailable_properties comes back non-empty (currently only "
                "'phonons'), say so explicitly -- but phrase it as 'not "
                "retrievable through this search API', not 'doesn't exist': "
                "the data may still be viewable by visiting the candidate's "
                "own url directly (e.g. Materials Project entries with a "
                "populated phonon_IDs field have a Phonons tab on their "
                "page; Materials Cloud's mc3d entries have their own phonon "
                "view). Point the user at the entry's url rather than "
                "implying the property is unavailable anywhere."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "formula": {
                        "type": "string",
                        "description": "Chemical formula, e.g. 'NaCl' or 'Fe2O3'.",
                    },
                    "properties": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "electronic",
                                "stability",
                                "magnetic",
                                "elastic",
                                "phonons",
                                "optical",
                            ],
                        },
                        "description": (
                            "Optional: only include these property "
                            "categories in the result (matches the search "
                            "panel's own filter chips) -- 'electronic' "
                            "(band gap), 'stability' (formation energy, "
                            "energy above hull), 'magnetic' (total "
                            "magnetization), 'elastic' (bulk/shear modulus), "
                            "'optical' (refractive index, dielectric "
                            "constant), 'phonons' (not retrievable as a "
                            "value through this API from any of these four "
                            "sources -- selecting it always comes back in "
                            "unavailable_properties, but the data may still "
                            "be viewable on the candidate's own page, see "
                            "unavailable_properties' note). Omit this to "
                            "return everything available."
                        ),
                    },
                },
                "required": ["formula"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_structure",
            "description": (
                "Fetch the full atomic structure (as CIF) for one specific "
                "candidate previously returned by find_in_databases. Only "
                "call this once the user has settled on which candidate to "
                "use, not for every candidate in a search result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": [
                            "Materials Project",
                            "Materials Cloud",
                            "NOMAD",
                            "JARVIS",
                        ],
                        "description": "Which database this candidate came from.",
                    },
                    "entry_id": {
                        "type": "string",
                        "description": (
                            "That database's id for the candidate, from "
                            "find_in_databases's result."
                        ),
                    },
                },
                "required": ["source", "entry_id"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    "find_in_databases": find_in_databases,
    "get_structure": get_structure,
}
