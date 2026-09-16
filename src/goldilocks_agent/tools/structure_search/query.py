"""Per-database query/fetch implementations backing the structure_search Tool.

Ported from old-goldilcoks-webapp/goldilocks-api/app/routes/{structure_match,
fetch_structure}.py -- **formula-search mode only**. The old "file" mode
(match an uploaded structure by formula+spacegroup) called
`goldilocks_core.structure.{features,io}` directly, but that's not actually a
core dependency: `load_structure` was `Structure.from_file(...)` and
`extract_match_features` was `SpacegroupAnalyzer` + `Composition` -- plain
pymatgen/spglib, the same operations `_optimade_to_structure`/
`_enrich_with_spacegroup` below already do with zero core involvement.
Current core dropped that code entirely during the v1->v2 rewrite (its own
docstring calls it "dead code, not an edge worth keeping"), and design doc
十四之三's "全部走 MCP" targets core's *domain decisions*
(advise/generate/capabilities), not generic file parsing -- file mode
doesn't need the core MCP client at all. It's just not built yet (2026-09-15:
correcting an earlier, wrong "blocked on core" note; server.py still 501s
`mode="file"` for now, but that's a scoping choice, not a real dependency).

None of these four sources needs its own MCP server (design doc's "all via
MCP" rule targets *core*, not arbitrary external services) -- Materials
Project needs a free (not paid) `MP_API_KEY`, JARVIS needs no key (static
dataset cached locally), Materials Cloud/NOMAD are public OPTIMADE endpoints.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import quote

import httpx
from pymatgen.core import Composition, Lattice, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.symmetry.groups import SpaceGroup

from goldilocks_agent.config import get_api_key
from goldilocks_agent.tools.structure_search import jarvis_cache
from goldilocks_agent.tools.structure_search.models import (
    FetchedStructure,
    MatchEntry,
    StructureMatchResult,
)

logger = logging.getLogger(__name__)

_OPTIMADE_TIMEOUT = 30.0
_FETCH_TIMEOUT = 30.0
_MAX_CANDIDATES = 20


# -- Formula normalization ----------------------------------------------------


def _to_optimade_formula(formula: str) -> str:
    """Normalize to OPTIMADE Hill format: C, H, alphabetical; no count-1 suffixes."""
    try:
        comp = Composition(formula).reduced_composition
        elements = sorted(
            comp.elements,
            key=lambda e: (e.symbol != "C", e.symbol != "H", e.symbol),
        )
        result = ""
        for el in elements:
            count = int(comp[el])
            result += el.symbol + (str(count) if count != 1 else "")
        return result
    except ValueError:
        return formula


def _formula_filter(formula_reduced: str) -> str:
    normalized = _to_optimade_formula(formula_reduced)
    return f'chemical_formula_reduced = "{normalized}"'


def parse_query_formula(raw: str) -> str:
    """Parse a user formula into pymatgen's reduced form. Raises ValueError if bad."""
    raw = raw.strip()
    for attempt in [raw, raw.title()]:
        try:
            return Composition(attempt).reduced_formula
        except ValueError:
            continue
    raise ValueError(f"Cannot parse formula: {raw!r}")


# -- Per-database queries ------------------------------------------------------


async def _query_optimade(
    base_url: str,
    source_name: str,
    formula_reduced: str,
    client: httpx.AsyncClient,
    url_builder,
    include_structure: bool = False,
    extra_fields: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    filter_str = _formula_filter(formula_reduced)
    base_fields = "id,chemical_formula_reduced,elements,nsites"
    if include_structure:
        base_fields += (
            ",lattice_vectors,species,species_at_sites,cartesian_site_positions"
        )
    if extra_fields:
        base_fields += "," + ",".join(extra_fields)
    params = {
        "filter": filter_str,
        "response_fields": base_fields,
        "page_limit": _MAX_CANDIDATES,
    }
    try:
        resp = await client.get(f"{base_url.rstrip('/')}/structures", params=params)
        if resp.status_code == 500:
            logger.warning(
                "%s returned 500 for formula=%s -- treating as empty",
                source_name,
                formula_reduced,
            )
            return [], None
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
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("%s query failed: %s", source_name, exc)
        return [], str(exc)


def _mc_url(item: dict, attrs: dict) -> str | None:
    mc_id = attrs.get("_mcloud_mc3d_id")
    if mc_id:
        return f"https://mc3d.materialscloud.org/details/{mc_id}/pbesol-v2"
    return None


def _nomad_url(item: dict, attrs: dict) -> str | None:
    return attrs.get("_nmd_entry_page_url") or attrs.get("_nmd_entry_id")


def _mp_vrh(value: dict | None) -> float | None:
    """MP reports elastic moduli as {"voigt", "reuss", "vrh"} -- VRH is the
    standard single-number average, when the material has elastic data at all."""
    return value.get("vrh") if isinstance(value, dict) else None


async def _query_mp(formula_reduced: str) -> tuple[list[dict], str | None]:
    api_key = get_api_key("materials_project")
    if not api_key:
        return [], "MP_API_KEY not configured"

    def _fetch():
        from mp_api.client import MPRester

        with MPRester(api_key) as mpr:
            docs = mpr.materials.summary.search(
                formula=formula_reduced,
                # Every field below verified 2026-09-15 against a real
                # mp-22862 (NaCl) call -- all in the same summary.search(),
                # no extra request per field.
                fields=[
                    "material_id",
                    "formula_pretty",
                    "symmetry",
                    "nsites",
                    "band_gap",
                    "formation_energy_per_atom",
                    "energy_above_hull",
                    "density",
                    "total_magnetization",
                    "bulk_modulus",
                    "shear_modulus",
                    "n",
                    "e_total",
                ],
            )
        entries = []
        for doc in docs[:_MAX_CANDIDATES]:
            mp_id = str(doc.material_id)
            spg = doc.symmetry.symbol if doc.symmetry else None
            entries.append(
                {
                    "item": {"id": mp_id},
                    "attrs": {
                        "chemical_formula_reduced": _to_optimade_formula(
                            doc.formula_pretty
                        ),
                        "_computed_spacegroup": spg,
                        "_computed_band_gap": doc.band_gap,
                        "_computed_formation_energy_per_atom": (
                            doc.formation_energy_per_atom
                        ),
                        "_computed_energy_above_hull": doc.energy_above_hull,
                        "_computed_density": doc.density,
                        "_computed_total_magnetization": doc.total_magnetization,
                        "_computed_bulk_modulus": _mp_vrh(doc.bulk_modulus),
                        "_computed_shear_modulus": _mp_vrh(doc.shear_modulus),
                        "_computed_refractive_index": doc.n,
                        "_computed_dielectric_constant": doc.e_total,
                    },
                    "url": f"https://next-gen.materialsproject.org/materials/{mp_id}",
                }
            )
        return entries, None

    try:
        # mp-api/MPRester doesn't expose a documented, stable exception
        # hierarchy for network/auth/rate-limit failures -- broad catch here
        # is intentional at this third-party SDK boundary, not laziness.
        return await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


async def _query_mc(
    formula_reduced: str, client: httpx.AsyncClient
) -> tuple[list[dict], str | None]:
    return await _query_optimade(
        "https://optimade.materialscloud.org/main/mc3d-pbesol-v2",
        "Materials Cloud",
        formula_reduced,
        client,
        _mc_url,
        # no native spacegroup field -- always fetch structure for local analysis
        include_structure=True,
        extra_fields=["_mcloud_mc3d_id"],
    )


async def _query_nomad(
    formula_reduced: str, client: httpx.AsyncClient
) -> tuple[list[dict], str | None]:
    return await _query_optimade(
        "https://nomad-lab.eu/prod/v1/optimade",
        "NOMAD",
        formula_reduced,
        client,
        _nomad_url,
        include_structure=False,  # native spacegroup field available
        extra_fields=[
            "_nmd_entry_page_url",
            "_nmd_results_material_symmetry_space_group_symbol",
            # Best-effort only -- verified 2026-09-15 against a real NOMAD
            # entry that OPTIMADE often lists a property as *available*
            # without exposing its value here, so this is commonly None.
            # Requesting it costs nothing when it isn't populated.
            "_nmd_results_properties_electronic_dos_electronic_band_gap_value",
            "_nmd_results_method_simulation_dft_xc_functional_type",
        ],
    )


def _spg_number_to_symbol(spg_raw) -> str | None:
    if spg_raw is None:
        return None
    try:
        return SpaceGroup.from_int_number(int(spg_raw)).symbol
    except (ValueError, TypeError):
        return str(spg_raw)


def _optimade_to_structure(attrs: dict) -> Structure | None:
    try:
        lattice = Lattice(attrs["lattice_vectors"])
        species = attrs["species_at_sites"]
        positions = attrs["cartesian_site_positions"]
        return Structure(lattice, species, positions, coords_are_cartesian=True)
    except (KeyError, ValueError, TypeError):
        return None


def _enrich_with_spacegroup(entries: list[dict]) -> None:
    """Compute spacegroup from structure data, store in attrs; modifies in place."""
    for entry in entries:
        s = _optimade_to_structure(entry["attrs"])
        if s is None:
            continue
        # SpacegroupAnalyzer wraps spglib (a C extension) -- no stable Python
        # exception type to narrow to; a degenerate/malformed structure just
        # means "skip enrichment for this one".
        with contextlib.suppress(Exception):
            analyzer = SpacegroupAnalyzer(s)
            entry["attrs"]["_computed_spacegroup"] = analyzer.get_space_group_symbol()


def _jarvis_float(item: dict, key: str) -> float | None:
    """JARVIS uses the string `"na"` for missing numeric fields, not null."""
    value = item.get(key)
    return value if isinstance(value, int | float) else None


def _jarvis_average(item: dict, *keys: str) -> float | None:
    """Average whichever of `keys` are actually reported (JARVIS's dielectric
    tensor components -- epsx/epsy/epsz -- are usually all-or-nothing, but
    average what's there rather than requiring all three)."""
    values = [v for k in keys if (v := _jarvis_float(item, k)) is not None]
    return sum(values) / len(values) if values else None


def _query_jarvis(formula_reduced: str) -> tuple[list[dict], str | None]:
    if not jarvis_cache.is_available():
        return [], "JARVIS cache not loaded"
    raw = jarvis_cache.query(formula_reduced, max_results=_MAX_CANDIDATES)
    entries = []
    for item in raw:
        jid = item.get("jid", "")
        if not jid:
            continue
        spg = _spg_number_to_symbol(item.get("spg"))
        entries.append(
            {
                "item": item,
                "attrs": {
                    "spg": spg,
                    "jid": jid,
                    "formula": item.get("formula", ""),
                    # verified 2026-09-15 against real cached NaCl entries --
                    # zero network cost, the whole entry is already in memory.
                    "band_gap": _jarvis_float(item, "optb88vdw_bandgap"),
                    "formation_energy_per_atom": _jarvis_float(
                        item, "formation_energy_peratom"
                    ),
                    "energy_above_hull": _jarvis_float(item, "ehull"),
                    "density": _jarvis_float(item, "density"),
                    "total_magnetization": _jarvis_float(item, "magmom_oszicar"),
                    "bulk_modulus": _jarvis_float(item, "bulk_modulus_kv"),
                    "shear_modulus": _jarvis_float(item, "shear_modulus_gv"),
                    # No direct refractive-index field at this tier -- left
                    # None rather than derived from epsilon (that would be an
                    # approximation not actually reported by the source).
                    "dielectric_constant": _jarvis_average(
                        item, "epsx", "epsy", "epsz"
                    ),
                },
                "url": f"https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{jid}.xml",
            }
        )
    return entries, None


# Every key here is a MatchEntry field name -- dict.fromkeys(...) below gives
# every source a None default, so each branch only has to set what it has.
_ENRICHMENT_FIELDS = (
    "band_gap",
    "band_gap_method",
    "formation_energy_per_atom",
    "energy_above_hull",
    "density",
    "total_magnetization",
    "bulk_modulus",
    "shear_modulus",
    "refractive_index",
    "dielectric_constant",
)


def _optimade_entry_to_result(entry: dict, source: str) -> MatchEntry:
    attrs = entry["attrs"]
    item = entry["item"]
    formula = attrs.get("chemical_formula_reduced", "")
    enrichment: dict[str, float | str | None] = dict.fromkeys(_ENRICHMENT_FIELDS)

    if source == "Materials Project":
        spg = attrs.get("_computed_spacegroup")
        entry_id = item.get("id")
        enrichment["band_gap"] = attrs.get("_computed_band_gap")
        if enrichment["band_gap"] is not None:
            enrichment["band_gap_method"] = "PBE"
        enrichment["formation_energy_per_atom"] = attrs.get(
            "_computed_formation_energy_per_atom"
        )
        enrichment["energy_above_hull"] = attrs.get("_computed_energy_above_hull")
        enrichment["density"] = attrs.get("_computed_density")
        enrichment["total_magnetization"] = attrs.get("_computed_total_magnetization")
        enrichment["bulk_modulus"] = attrs.get("_computed_bulk_modulus")
        enrichment["shear_modulus"] = attrs.get("_computed_shear_modulus")
        enrichment["refractive_index"] = attrs.get("_computed_refractive_index")
        enrichment["dielectric_constant"] = attrs.get("_computed_dielectric_constant")
    elif source == "Materials Cloud":
        spg = attrs.get("_computed_spacegroup")
        entry_id = attrs.get("_mcloud_mc3d_id")
    else:  # NOMAD
        spg = attrs.get(
            "_nmd_results_material_symmetry_space_group_symbol"
        ) or attrs.get("_computed_spacegroup")
        entry_id = item.get("id")
        enrichment["band_gap"] = attrs.get(
            "_nmd_results_properties_electronic_dos_electronic_band_gap_value"
        )
        if enrichment["band_gap"] is not None:
            enrichment["band_gap_method"] = attrs.get(
                "_nmd_results_method_simulation_dft_xc_functional_type"
            )

    return MatchEntry(
        formula=formula,
        spacegroup=spg,
        entry_id=entry_id,
        source=source,
        url=entry["url"],
        matched=False,
        score=None,
        **enrichment,
    )


def _jarvis_entry_to_result(entry: dict) -> MatchEntry:
    attrs = entry["attrs"]
    band_gap = attrs.get("band_gap")
    return MatchEntry(
        formula=attrs.get("formula", ""),
        spacegroup=attrs.get("spg"),
        entry_id=attrs.get("jid"),
        source="JARVIS",
        url=entry["url"],
        matched=False,
        score=None,
        band_gap=band_gap,
        band_gap_method="OptB88vdW" if band_gap is not None else None,
        formation_energy_per_atom=attrs.get("formation_energy_per_atom"),
        energy_above_hull=attrs.get("energy_above_hull"),
        density=attrs.get("density"),
        total_magnetization=attrs.get("total_magnetization"),
        bulk_modulus=attrs.get("bulk_modulus"),
        shear_modulus=attrs.get("shear_modulus"),
        dielectric_constant=attrs.get("dielectric_constant"),
    )


async def search_by_formula(formula: str) -> StructureMatchResult:
    """Query all four databases in parallel for a formula. No core dependency."""
    query_formula = parse_query_formula(formula)
    errors: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=_OPTIMADE_TIMEOUT) as client:
        (
            (mp_entries, mp_err),
            (mc_entries, mc_err),
            (nomad_entries, nomad_err),
        ) = await asyncio.gather(
            _query_mp(query_formula),
            _query_mc(query_formula, client),
            _query_nomad(query_formula, client),
        )

    if mp_err:
        errors["Materials Project"] = mp_err
    if mc_err:
        errors["Materials Cloud"] = mc_err
    if nomad_err:
        errors["NOMAD"] = nomad_err

    jarvis_entries, jarvis_err = _query_jarvis(query_formula)
    if jarvis_err:
        errors["JARVIS"] = jarvis_err

    if mc_entries:
        _enrich_with_spacegroup(mc_entries)

    results = (
        [_optimade_entry_to_result(e, "Materials Project") for e in mp_entries]
        + [_optimade_entry_to_result(e, "Materials Cloud") for e in mc_entries]
        + [_optimade_entry_to_result(e, "NOMAD") for e in nomad_entries]
        + [_jarvis_entry_to_result(e) for e in jarvis_entries]
    )

    return StructureMatchResult(
        results=results, query_formula=query_formula, errors=errors
    )


# -- Fetch a single structure as CIF ------------------------------------------


def _structure_from_optimade_attrs(attrs: dict) -> str:
    lattice = Lattice(attrs["lattice_vectors"])
    species = attrs["species_at_sites"]
    positions = attrs["cartesian_site_positions"]
    return Structure(lattice, species, positions, coords_are_cartesian=True).to(
        fmt="cif"
    )


async def _fetch_mp(entry_id: str) -> FetchedStructure:
    api_key = get_api_key("materials_project")
    if not api_key:
        raise RuntimeError("MP_API_KEY not configured")

    def _sync():
        from mp_api.client import MPRester

        with MPRester(api_key) as mpr:
            struct = mpr.get_structure_by_material_id(entry_id)
        if struct is None:
            raise ValueError(f"No structure returned for {entry_id!r}")
        return struct.to(fmt="cif")

    cif = await asyncio.to_thread(_sync)
    return FetchedStructure(filename=f"{entry_id}.cif", content=cif)


def _fetch_jarvis(entry_id: str) -> FetchedStructure:
    if not jarvis_cache.is_available():
        raise RuntimeError("JARVIS cache not loaded")
    entry = jarvis_cache.get_by_jid(entry_id)
    if entry is None:
        raise LookupError(f"JARVIS entry not found: {entry_id!r}")
    atoms = entry.get("atoms")
    if not atoms:
        raise LookupError(f"No structure data for JARVIS entry {entry_id!r}")

    lattice = Lattice(atoms["lattice_mat"])
    struct = Structure(
        lattice,
        atoms["elements"],
        atoms["coords"],
        coords_are_cartesian=atoms.get("cartesian", False),
    )
    return FetchedStructure(filename=f"{entry_id}.cif", content=struct.to(fmt="cif"))


async def _fetch_mc(entry_id: str) -> FetchedStructure:
    fields = (
        "id,chemical_formula_reduced,lattice_vectors,"
        "species_at_sites,cartesian_site_positions"
    )
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        resp = await client.get(
            "https://optimade.materialscloud.org/main/mc3d-pbesol-v2/structures",
            params={
                "filter": f'_mcloud_mc3d_id="{entry_id}"',
                "response_fields": fields,
            },
        )
        resp.raise_for_status()
    items = resp.json().get("data", [])
    if not items:
        raise LookupError(f"No Materials Cloud entry found for {entry_id!r}")
    cif = _structure_from_optimade_attrs(items[0]["attributes"])
    return FetchedStructure(filename=f"{entry_id}.cif", content=cif)


async def _fetch_nomad(entry_id: str) -> FetchedStructure:
    fields = (
        "lattice_vectors,species_at_sites,"
        "cartesian_site_positions,chemical_formula_reduced"
    )
    url = f"https://nomad-lab.eu/prod/v1/optimade/structures/{quote(entry_id, safe='')}"
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        resp = await client.get(url, params={"response_fields": fields})
        if resp.status_code == 404:
            raise LookupError(f"NOMAD entry not found: {entry_id!r}")
        resp.raise_for_status()
    attrs = resp.json().get("data", {}).get("attributes", {})
    safe_id = entry_id.replace("/", "-")[:40]
    cif = _structure_from_optimade_attrs(attrs)
    return FetchedStructure(filename=f"nomad-{safe_id}.cif", content=cif)


async def fetch_structure(source: str, entry_id: str) -> FetchedStructure:
    """Fetch one structure as CIF. Raises LookupError or ValueError (bad source)."""
    if source == "Materials Project":
        return await _fetch_mp(entry_id)
    if source == "JARVIS":
        return await asyncio.to_thread(_fetch_jarvis, entry_id)
    if source == "Materials Cloud":
        return await _fetch_mc(entry_id)
    if source == "NOMAD":
        return await _fetch_nomad(entry_id)
    raise ValueError(f"Unknown source: {source!r}")
