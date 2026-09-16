"""Find in Databases: search Materials Project, Materials Cloud, NOMAD, and
JARVIS for existing structures by chemical formula (design doc 十二 ·
implementation plan 第 5 步 area).

This is the Tool's public surface -- callers (`server.py`, tests) import
from here, not from `query`/`models` directly, so the LLM-facing tool
schema (once it lands, alongside `search_by_formula`/`fetch_structure`) has
one place to live next to the functions it describes.
"""

from __future__ import annotations

from goldilocks_agent.tools.structure_search.grouping import (
    filter_by_properties,
    group_candidates,
)
from goldilocks_agent.tools.structure_search.models import (
    CandidateGroup,
    FetchedStructure,
    GroupedSearchResult,
    MatchEntry,
    StructureMatchResult,
)
from goldilocks_agent.tools.structure_search.query import (
    fetch_structure,
    parse_query_formula,
    search_by_formula,
)
from goldilocks_agent.tools.structure_search.tool import find_in_databases

__all__ = [
    "CandidateGroup",
    "FetchedStructure",
    "GroupedSearchResult",
    "MatchEntry",
    "StructureMatchResult",
    "fetch_structure",
    "filter_by_properties",
    "find_in_databases",
    "group_candidates",
    "parse_query_formula",
    "search_by_formula",
]
