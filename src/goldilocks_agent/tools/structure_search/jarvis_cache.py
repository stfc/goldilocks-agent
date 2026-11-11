"""JARVIS DFT-3D local cache -- download once, query in-process.

Ported from old-goldilcoks-webapp/goldilocks-api/app/services/jarvis_cache.py
unchanged: JARVIS has no API/key, the dataset is small enough to cache whole.
No `goldilocks-core` dependency here -- this only backs the "formula" search
mode (see query.py), never the "file" match mode.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_FIGSHARE_URL = "https://ndownloader.figshare.com/files/38521619"
_CACHE_DIR = Path.home() / ".cache" / "goldilocks"
_CACHE_FILE = _CACHE_DIR / "jarvis_dft_3d.json"

_entries: list[dict] | None = None
# Entries by reduced formula, the lowest formation energy first; built on the
# first query (parsing ~76k formulas takes about a second) and reset whenever
# _entries is.
_by_formula: dict[str, list[dict]] | None = None


def is_available() -> bool:
    return _entries is not None


def load() -> None:
    """Load JARVIS data from disk cache into memory. No-op if already loaded."""
    global _entries, _by_formula
    if _entries is not None:
        return
    if not _CACHE_FILE.exists():
        logger.warning(
            "JARVIS cache not found at %s -- run `uv run poe fetch-jarvis-cache` first",
            _CACHE_FILE,
        )
        return
    logger.info("Loading JARVIS DFT-3D cache from %s ...", _CACHE_FILE)
    with open(_CACHE_FILE) as f:
        _entries = json.load(f)
    _by_formula = None
    logger.info("Loaded %d JARVIS entries", len(_entries))


async def download_cache() -> None:
    """Download the JARVIS DFT-3D dataset from Figshare and save to disk cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading JARVIS DFT-3D from %s ...", _FIGSHARE_URL)
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.get(_FIGSHARE_URL, follow_redirects=True)
        response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        json_name = next(n for n in zf.namelist() if n.endswith(".json"))
        data = json.loads(zf.read(json_name))

    with open(_CACHE_FILE, "w") as f:
        json.dump(data, f)

    global _entries, _by_formula
    _entries = data
    _by_formula = None
    logger.info("JARVIS cache saved (%d entries)", len(_entries))


def get_by_jid(jid: str) -> dict | None:
    """Return the full JARVIS entry for a given JID, or None if not found."""
    if _entries is None:
        return None
    for entry in _entries:
        if entry.get("jid") == jid:
            return entry
    return None


def query(formula_reduced: str, max_results: int = 20) -> list[dict]:
    """Return JARVIS entries whose reduced formula matches the query, the
    lowest formation energy first, so capping at `max_results` never drops
    the most stable one. Formation energy, not JARVIS's ehull, which reads
    0.0 even for silicon polymorphs 0.7 eV/atom above diamond."""
    from pymatgen.core import Composition

    if _entries is None:
        return []

    try:
        target = Composition(formula_reduced).reduced_formula
    except ValueError:
        return []
    return _index().get(target, [])[:max_results]


def _index() -> dict[str, list[dict]]:
    global _by_formula
    if _by_formula is None:
        from pymatgen.core import Composition

        index: dict[str, list[dict]] = {}
        for entry in _entries or []:
            try:
                reduced = Composition(entry.get("formula", "")).reduced_formula
            except ValueError:
                continue
            index.setdefault(reduced, []).append(entry)
        for entries in index.values():
            entries.sort(key=_formation_energy)
        _by_formula = index
    return _by_formula


def _formation_energy(entry: dict) -> float:
    """An entry's formation energy per atom; inf when JARVIS has none ("na")."""
    value = entry.get("formation_energy_peratom")
    return value if isinstance(value, int | float) else float("inf")
