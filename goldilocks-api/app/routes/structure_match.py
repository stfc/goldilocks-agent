"""Structure search across Materials Project, Materials Cloud, NOMAD, and JARVIS."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymatgen.core import Composition, Lattice, Structure

from app.services import jarvis_cache
from goldilocks_core.structure.features import extract_match_features
from goldilocks_core.structure.match import MatchResult, run_matcher
from goldilocks_core.structure.io import load_structure

logger = logging.getLogger(__name__)
router = APIRouter()

_OPTIMADE_TIMEOUT = 30.0
_MAX_CANDIDATES = 20


# ── Request / Response ────────────────────────────────────────────────────────

class StructureMatchRequest(BaseModel):
    mode: str  # "file" | "formula"
    structure_content: str | None = None
    structure_name: str | None = None
    formula: str | None = None


class MatchEntry(BaseModel):
    formula: str
    spacegroup: str | None
    entry_id: str | None
    source: str
    url: str
    matched: bool
    score: float | None


class StructureMatchResponse(BaseModel):
    results: list[MatchEntry]
    query_formula: str | None
    errors: dict[str, str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_structure(content: str, name: str) -> Structure:
    suffix = Path(name).suffix or ".cif"
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(content)
        tmp_path = f.name
    try:
        return load_structure(tmp_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _optimade_to_structure(attrs: dict) -> Structure | None:
    try:
        lattice = Lattice(attrs["lattice_vectors"])
        species = attrs["species_at_sites"]
        positions = attrs["cartesian_site_positions"]
        return Structure(lattice, species, positions, coords_are_cartesian=True)
    except Exception:
        return None


def _jarvis_to_structure(entry: dict) -> Structure | None:
    try:
        atoms = entry["atoms"]
        lattice = Lattice(atoms["lattice_mat"])
        return Structure(
            lattice,
            atoms["elements"],
            atoms["coords"],
            coords_are_cartesian=atoms.get("cartesian", False),
        )
    except Exception:
        return None


def _formula_filter(formula_reduced: str) -> str:
    return f'chemical_formula_reduced = "{formula_reduced}"'


# ── Per-database queries ──────────────────────────────────────────────────────

async def _query_optimade(
    base_url: str,
    source_name: str,
    formula_reduced: str,
    client: httpx.AsyncClient,
    url_builder,
    include_structure: bool = False,
) -> tuple[list[dict], str | None]:
    """Query an OPTIMADE endpoint, return (raw_entries, error)."""
    filter_str = _formula_filter(formula_reduced)
    base_fields = "id,chemical_formula_reduced,elements,nsites"
    if include_structure:
        base_fields += ",lattice_vectors,species,species_at_sites,cartesian_site_positions"
    params = {
        "filter": filter_str,
        "response_fields": base_fields,
        "page_limit": _MAX_CANDIDATES,
    }
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/structures", params=params)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("data", [])
        entries = []
        for item in raw:
            attrs = item.get("attributes", {})
            url = url_builder(item, attrs)
            if url:
                entries.append({"item": item, "attrs": attrs, "url": url})
        return entries, None
    except Exception as exc:
        return [], str(exc)


def _mp_url(item: dict, attrs: dict) -> str | None:
    entry_id = item.get("id", "")
    if entry_id:
        return f"https://next-gen.materialsproject.org/materials/{entry_id}"
    return None


def _mc_url(item: dict, attrs: dict) -> str | None:
    mc_id = attrs.get("_mcloud_mc3d_id")
    if mc_id:
        return f"https://mc3d.materialscloud.org/details/{mc_id}/pbesol-v2"
    return None


def _nomad_url(item: dict, attrs: dict) -> str | None:
    return attrs.get("_nmd_entry_page_url") or attrs.get("_nmd_entry_id")


async def _query_mp(formula_reduced: str, client: httpx.AsyncClient, include_structure: bool = False):
    return await _query_optimade(
        "https://optimade.materialsproject.org/v1",
        "Materials Project",
        formula_reduced,
        client,
        _mp_url,
        include_structure=include_structure,
    )


async def _query_mc(formula_reduced: str, client: httpx.AsyncClient, include_structure: bool = False):
    return await _query_optimade(
        "https://optimade.materialscloud.org/main/mc3d-pbesol-v2",
        "Materials Cloud",
        formula_reduced,
        client,
        _mc_url,
        include_structure=include_structure,
    )


async def _query_nomad(formula_reduced: str, client: httpx.AsyncClient, include_structure: bool = False):
    return await _query_optimade(
        "https://nomad-lab.eu/prod/v1/optimade",
        "NOMAD",
        formula_reduced,
        client,
        _nomad_url,
        include_structure=include_structure,
    )


def _spg_number_to_symbol(spg_raw) -> str | None:
    if spg_raw is None:
        return None
    try:
        from pymatgen.symmetry.groups import SpaceGroup
        return SpaceGroup.from_int_number(int(spg_raw)).symbol
    except Exception:
        return str(spg_raw)


def _query_jarvis_sync(formula_reduced: str) -> tuple[list[dict], str | None]:
    if not jarvis_cache.is_available():
        return [], "JARVIS cache not loaded"
    raw = jarvis_cache.query(formula_reduced, max_results=_MAX_CANDIDATES)
    entries = []
    for item in raw:
        jid = item.get("jid", "")
        formula = item.get("formula", "")
        spg = _spg_number_to_symbol(item.get("spg"))
        url = f"https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{jid}.xml" if jid else None
        if url:
            entries.append({"item": item, "attrs": {"spg": spg, "jid": jid, "formula": formula}, "url": url})
    return entries, None


# ── Conversion to MatchResult (without StructureMatcher) ─────────────────────

def _optimade_entry_to_result(entry: dict, source: str) -> MatchEntry:
    attrs = entry["attrs"]
    formula = attrs.get("chemical_formula_reduced", "")
    spg = attrs.get("_mp_symmetry_info", {}).get("symbol") if source == "Materials Project" else None
    return MatchEntry(
        formula=formula,
        spacegroup=spg,
        entry_id=None,
        source=source,
        url=entry["url"],
        matched=False,
        score=None,
    )


def _jarvis_entry_to_result(entry: dict) -> MatchEntry:
    attrs = entry["attrs"]
    return MatchEntry(
        formula=attrs.get("formula", ""),
        spacegroup=attrs.get("spg"),
        entry_id=attrs.get("jid"),
        source="JARVIS",
        url=entry["url"],
        matched=False,
        score=None,
    )


# ── File-mode: run StructureMatcher ──────────────────────────────────────────

def _run_structure_matcher(
    query: Structure,
    optimade_entries: list[tuple[str, list[dict]]],  # (source, entries)
    jarvis_entries: list[dict],
) -> list[MatchEntry]:
    candidates: list[tuple[Structure, dict]] = []

    for source, entries in optimade_entries:
        for entry in entries:
            s = _optimade_to_structure(entry["attrs"])
            if s is not None:
                candidates.append((s, {
                    "formula": entry["attrs"].get("chemical_formula_reduced", ""),
                    "spacegroup": None,
                    "source": source,
                    "url": entry["url"],
                }))

    for entry in jarvis_entries:
        s = _jarvis_to_structure(entry["item"])
        if s is not None:
            candidates.append((s, {
                "formula": entry["attrs"].get("formula", ""),
                "spacegroup": entry["attrs"].get("spg"),
                "source": "JARVIS",
                "url": entry["url"],
            }))

    match_results = run_matcher(query, candidates)
    return [
        MatchEntry(
            formula=r.formula,
            spacegroup=r.spacegroup,
            entry_id=None,
            source=r.source,
            url=r.url,
            matched=r.matched,
            score=r.score,
        )
        for r in match_results
    ]


# ── Main route ────────────────────────────────────────────────────────────────

@router.post("/structure-match", response_model=StructureMatchResponse)
async def structure_match(req: StructureMatchRequest):
    errors: dict[str, str] = {}

    if req.mode == "file":
        if not req.structure_content or not req.structure_name:
            raise HTTPException(400, "structure_content and structure_name required for file mode")
        query_structure = _parse_structure(req.structure_content, req.structure_name)
        features = extract_match_features(query_structure)
        elements = features.elements
        query_formula = features.formula_reduced
    elif req.mode == "formula":
        if not req.formula:
            raise HTTPException(400, "formula required for formula mode")
        try:
            comp = Composition(req.formula)
            elements = sorted(comp.chemical_system.split("-"))
            query_formula = comp.reduced_formula
        except Exception as exc:
            raise HTTPException(422, f"Cannot parse formula: {exc}") from exc
        query_structure = None
    else:
        raise HTTPException(400, f"Unknown mode: {req.mode!r}")

    # Parallel DB queries
    include_structure = (req.mode == "file")
    async with httpx.AsyncClient(timeout=_OPTIMADE_TIMEOUT) as client:
        mp_task = _query_mp(query_formula, client, include_structure)
        mc_task = _query_mc(query_formula, client, include_structure)
        nomad_task = _query_nomad(query_formula, client, include_structure)

        (mp_entries, mp_err), (mc_entries, mc_err), (nomad_entries, nomad_err) = (
            await asyncio.gather(mp_task, mc_task, nomad_task)
        )

    if mp_err:
        errors["Materials Project"] = mp_err
    if mc_err:
        errors["Materials Cloud"] = mc_err
    if nomad_err:
        errors["NOMAD"] = nomad_err

    jarvis_entries, jarvis_err = _query_jarvis_sync(query_formula)
    if jarvis_err:
        errors["JARVIS"] = jarvis_err

    # Build results
    if req.mode == "file" and query_structure is not None:
        results = _run_structure_matcher(
            query_structure,
            [
                ("Materials Project", mp_entries),
                ("Materials Cloud", mc_entries),
                ("NOMAD", nomad_entries),
            ],
            jarvis_entries,
        )
    else:
        results = (
            [_optimade_entry_to_result(e, "Materials Project") for e in mp_entries]
            + [_optimade_entry_to_result(e, "Materials Cloud") for e in mc_entries]
            + [_optimade_entry_to_result(e, "NOMAD") for e in nomad_entries]
            + [_jarvis_entry_to_result(e) for e in jarvis_entries]
        )

    return StructureMatchResponse(
        results=results,
        query_formula=query_formula,
        errors=errors,
    )
