# Structure Match — Design Document

## Overview

A sidebar tool that takes the currently loaded structure and searches for matching or similar structures across multiple external databases and a local database.

---

## Data Sources

| Source | Access Method | Notes |
|---|---|---|
| Materials Project | `mp-api` (official Python client) | API key required |
| Materials Cloud | OPTIMADE — `mc3d-pbesol-v2` endpoint (primary) | Confirmed working |
| NOMAD | OPTIMADE — `https://nomad-lab.eu/prod/v1/optimade` | Confirmed working |
| JARVIS | figshare bulk download, cached locally | ~40 MB compressed; see caching strategy below |
| Local database | Custom query | Not yet set up — deferred |

Each source uses its most reliable native interface.

### Materials Cloud — dataset selection

Materials Cloud hosts multiple OPTIMADE sub-databases. For 3D bulk structure matching, query `mc3d-pbesol-v2` (most recent, PBEsol functional). `mc3d-pbe-v1` may be queried in parallel as a secondary source once the primary is working.

Available endpoints (for reference):

```
https://optimade.materialscloud.org/main/mc3d-pbesol-v2/   ← primary
https://optimade.materialscloud.org/main/mc3d-pbe-v1/
https://optimade.materialscloud.org/main/mc3d-pbesol-v1/
https://optimade.materialscloud.org/main/mc2d/
https://optimade.materialscloud.org/main/2dtopo/
https://optimade.materialscloud.org/main/pyrene-mofs/
https://optimade.materialscloud.org/main/curated-cofs/
https://optimade.materialscloud.org/main/autowannier/
```

### JARVIS — caching strategy

JARVIS has no OPTIMADE endpoint and no suitable REST API for live queries. The `dft_3d` dataset is fetched once from Figshare (~40 MB compressed, ~200 MB JSON) and cached to `~/.cache/goldilocks/jarvis_dft_3d.json`. goldilocks-api loads this cache into memory at startup. All subsequent JARVIS queries run in-process without any network calls.

Cache is **not** bundled inside the Python package — it is user-local data, not code.

---

## Matching Strategy

External databases do not support server-side structure matching. The approach is:

1. **Filter** — query each source using cheap filters (chemical formula, number of sites, elements) to narrow down candidates
2. **Fetch** — download candidate structures
3. **Match** — run `pymatgen StructureMatcher` locally on candidates to find true structural equivalents
4. **Rank** — sort results by similarity score

```
User loads structure
    ↓
goldilocks-core: extract features (formula, spacegroup, nsites, elements)
    ↓         ↓            ↓           ↓
  mp-api   MC OPTIMADE  NOMAD OPTIMADE  JARVIS (local cache)
    ↓         ↓            ↓           ↓
         parallel queries — filter by formula / nsites
    ↓
goldilocks-core: run pymatgen StructureMatcher on candidates
    ↓
ranked results returned to frontend
```

---

## Trigger

Search can be triggered via two paths:

1. **"Search databases" button in the Structure Match panel** — primary trigger. Uses the structure file currently selected in "Structures in Current Chat". Disabled when no structure is present in the chat.
2. **"Search" button in the Periodic Table formula builder** — triggers an element-set search directly from the Periodic Table popup, without requiring a structure file.

Automatic triggering on every structure load is avoided — it would fire 4+ external requests on every upload, adding latency when the user does not need a match.

---

## Frontend UI

### Structure Match panel (right sidebar)

Opens when the user activates Structure Match mode. No tabs — content is displayed directly.

**Query structure section:**
- Displays the structure file currently selected in "Structures in Current Chat". No re-upload needed; the panel reads directly from structures already in the chat transcript.
- If the chat contains multiple structure files, ‹ › arrows let the user switch between them.
- Shows the filename and a "Pending" badge if the file has been attached but not yet sent.
- "Search databases" button is disabled when no structure file is in the chat.

**Candidate structures section:**
- Each result card shows: formula (bold) · space group · source database (muted) · clickable link to the entry page.
- The Viewer tab has been removed from this panel. Structure visualisation is available via the Structure Viewer in the composer area.

### Structures in Current Chat

Renamed from "Chat Files". Lists all structure files in the current chat:
- Files attached but not yet sent (marked "Pending")
- Files already sent in previous messages (extracted from the message transcript)

This list is shared between the Structure Match panel (query structure selection) and the Structure Viewer.

### Periodic Table formula builder

The Periodic Table popup includes a formula accumulator placed in the U-shaped blank area between H and He (row 1, columns 2–17 of the 18-column grid).

- **Click an element** → adds it to the formula. Clicking the same element again increments its count (Fe → Fe2 → Fe3).
- **Code block** — displays the current formula (e.g. `Fe2 O`) in the blank area at the top of the table.
- **↵ button** — inserts the formula string into the chat input and closes the picker.
- **Search button** — directly activates Structure Match and triggers an element-set search (no structure file required).
- **✕ button** — clears the current formula.

### Two search modes

| Mode | Input | Filter applied | StructureMatcher | Result breadth |
|---|---|---|---|---|
| File-based | Uploaded structure file | Formula + nsites + elements | Yes (precise match) | Narrow |
| Formula-based | Element set from Periodic Table | Elements only (no stoichiometry) | No | Broad |

Formula-based search returns all database entries containing the selected elements regardless of stoichiometry. Results will be more numerous. The UI should make this distinction clear when displaying results (deferred — to be designed when the backend is wired up).

---

## Results Display

### Right panel (Structure Match tool)

Displays a compact list of matches. Each row shows:

| Field | Notes |
|---|---|
| Formula | Chemical formula of the matched structure |
| Source | Database name (MP, Materials Cloud, NOMAD, JARVIS) |
| Link | Clickable URL to the entry in the source database |

### Link URL patterns

| Source | How to get the URL | Example |
|---|---|---|
| Materials Project | construct from `material_id` field | `https://next-gen.materialsproject.org/materials/mp-149` |
| NOMAD | read `_nmd_entry_page_url` attribute directly — no construction needed | `https://nomad-lab.eu/prod/v1/gui/entry/id/{upload_id}/{entry_id}` |
| Materials Cloud | construct from `_mcloud_mc3d_id` attribute | `https://mc3d.materialscloud.org/mc3d-23122` |
| JARVIS | construct from `jid` field | `https://jarvis.nist.gov/?jid=JVASP-14831` |

Notes:
- NOMAD's URL embeds both `upload_id` and `entry_id` — the full URL is already in the `_nmd_entry_page_url` attribute, so no construction logic is needed.
- Materials Cloud's `links` field is null in the OPTIMADE response; the usable web URL comes from the provider-specific `_mcloud_mc3d_id` attribute.
- JARVIS `/?jid=` returns HTTP 200 and opens the correct entry page in a browser (SPA — content is JS-rendered, not in HTML source).

### Chat (LLM)

Match results are also passed as context to the LLM. The LLM provides natural-language analysis in the chat panel. The two surfaces are complementary: the right panel gives the raw list for quick scanning, the chat gives interpretation.

---

## Code Organisation

### goldilocks-core (computation only, no network)

New files to add under `structure/`:

| File | Contents |
|---|---|
| `structure/features.py` | `extract_match_features()` — returns formula, spacegroup, nsites, elements |
| `structure/match.py` | `run_matcher()`, `MatchResult` dataclass |

### goldilocks-api (all network calls)

New route:

```
routes/structure_match.py
    POST /api/structure-match
        — parallel queries to MP, MC OPTIMADE, NOMAD OPTIMADE, JARVIS cache
        — calls goldilocks-core for StructureMatcher
        — returns ranked MatchResult list with links
```

All external API calls (mp-api, OPTIMADE requests, JARVIS cache load) live in goldilocks-api. goldilocks-core stays pure computation with no network dependencies.

---

## Still To Decide

- Local database: schema and query interface (deferred until DB is set up)

---

## Beyond DFT Methods

The Beyond DFT tool covers methods that go beyond standard DFT. Grouped by category:

| Category | Methods |
|---|---|
| Correlated and corrected DFT | DFT+U, DFT+DMFT, Hybrid functionals (PBE0/HSE06), SIC |
| Many-body perturbation theory | GW, BSE, RPA, MP2 |
| Time-dependent | TDDFT |
| Quantum Monte Carlo | QMC (DMC/VMC) |
| Model and embedding | MFT, Wannier functions, QM/MM |

**DFT+DMFT**: Dynamical mean-field theory embedded in DFT. Captures dynamic correlations in strongly correlated d/f-electron systems (e.g. transition metal oxides, heavy fermions) beyond the static DFT+U picture. Typically requires codes such as TRIQS, Wien2k+DMFT, or VASP+DMFT.

**QM/MM**: Quantum mechanics / molecular mechanics. Embeds a DFT region in a classical force-field environment for large systems where full DFT would be prohibitively expensive — common for surface reactions, zeolites, and biomolecules. Supported by CP2K, ONIOM (Gaussian), and similar frameworks.
