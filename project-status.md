# Goldilocks — Project Status & Roadmap

*Last updated: 2026-04-25*

This document records the complete current state of every component, what is real vs mock, and what needs to be built before each capability is functional end-to-end.

---

## 1. System Architecture

```
goldilocks-web  (React/Vite, browser)
      │
      │  POST /api/chat          → SSE streaming  ✅ working
      │  POST /api/structure-match               ✗ not built
      │  POST /api/dft/kpoints                   ✗ not built
      │  POST /api/dft/pseudo                    ✗ not built
      │  POST /api/mlip/singlepoint              ✗ not built
      ▼
goldilocks-api  (FastAPI)
      │
      ├── import goldilocks-core  (in-process)   △ partial
      ├──→ vLLM  /v1/chat/completions            ✅ working
      └──→ janus-api                             ✗ not built
```

| Package | Role | Status |
|---|---|---|
| `goldilocks-web` | React/Vite frontend | UI complete; all tools stubbed/mock |
| `goldilocks-api` | FastAPI orchestration | Chat only; no DFT/MLIP/structure-match routes |
| `goldilocks-core` | Python computation | k-mesh + pseudo working; structure match missing |
| vLLM | Qwen3 inference on STFC cloud | Running; no system prompt, no tool calling |
| `janus-api` | MLIP HTTP wrapper (janus-core) | Not yet created |

---

## 2. goldilocks-web — Frontend

### 2.1 What is fully built

| Area | Details |
|---|---|
| Shell | Dark/light theme, left sidebar, right panel, responsive layout |
| Session management | Create / delete / rename sessions; project grouping |
| Chat | SSE streaming from `/api/chat`; user messages, assistant messages, typing indicator |
| Structure attachment | CIF/POSCAR/XYZ file attach via clip button or drag-and-drop |
| Structure viewer | 3D WEAS-based viewer in composer area; file list "Structures in Current Chat" |
| Periodic table | Element picker with formula accumulator (click to build Fe₂O₃ style strings) |
| Mode launcher | 4 mode cards: Find in Databases, MLIP Playground, DFT Workspace, Beyond DFT |
| DFT Workspace panel | WorkspacePickers: Code / Functional / Pseudopotential / Task / Machine |
| Beyond DFT panel | WorkspacePickers: Method / Code / Machine |
| ✦ Recommend button | Auto-selects recommended item; generates explanation prompt in chat input |
| ? Ask button | Generates "what should I know about X?" prompt for any picker option |
| Find in Databases panel | Query structure display (from chat files); navigation arrows; "Search databases" button |
| MLIP Playground panel | Model selector (MACE/CHGNet/ALIGNN); Analysis / Metrics / Compute tabs |
| Experience onboarding | One-time level picker (New / Familiar / Advanced); stored in localStorage |
| Settings overlay | Theme toggle, experience level, model selector |
| State persistence | Sessions, theme, experience level stored in localStorage |

### 2.2 What is stubbed / mock

| Area | What it does now | What it should do |
|---|---|---|
| "Search databases" button | 2-second fake loading spinner | POST `/api/structure-match`, render real results |
| Candidate structures | 3 hardcoded cards (Fe2O3, SrTiO3, TiO2) | Render API response dynamically |
| Formula search (Periodic Table → Search) | Fake 2-second loading | POST `/api/structure-match` with element set |
| DFT Inputs tab | Hardcoded `dftGuidancePreview` string | Render actual LLM-generated or API-generated input file content |
| DFT Checks tab | Static text snippets | Wire to validation logic |
| MLIP metrics (MAE, RMSE) | Hardcoded 0.041 / 0.063 / 0.118 | Computed from janus-api response |
| MLIP compute | Hardcoded reference/predicted/delta | From janus-api single-point result |
| Model selector | UI only — `selectedModel` not sent to API | Include `model` in `/api/chat` request body |
| Mode context injection | `[Mode: DFT Workspace]` prefix prepended to user message | Full system prompt with workspace state (code, task, structure) |

### 2.3 State that the frontend manages

```
session.messages          full chat history (role + content + display)
attachedFile              pending structure file (before send)
chatStructures            all structure files extracted from message history
viewerIdx                 which structure is shown in viewer / used for match query
selectedDftCode           Code picker value
selectedDftFunctional     Functional picker value
selectedDftPseudo         Pseudopotential picker value
selectedDftTask           Task picker value
selectedDftMachine        Machine picker value
selectedBeyondDftMethod   Beyond DFT method picker value
selectedMlipModel         MLIP model selection
input                     chat input box content
loading                   true while SSE stream is active
structureMatchLoading     true while structure match is running
```

---

## 3. goldilocks-api — Backend

### 3.1 What is built

| Route | Status | Details |
|---|---|---|
| `POST /api/chat` | ✅ | SSE stream proxied to vLLM; `<think>` tokens filtered; system prompt injected |
| `GET /api/health` | ✅ | Returns vLLM URL and model |
| `POST /api/dft/kpoints` | ✅ | Returns k-mesh grid + irreducible k-point count for a given structure and k-distance |
| `POST /api/dft/pseudo` | ✅ | Returns pseudopotential candidates per element filtered by functional/type |

**Existing capabilities:**
- httpx async client with streaming
- `_filter_think()` strips reasoning tokens from Qwen3 chain-of-thought
- Model auto-detection via `/v1/models` when `VLLM_MODEL` is unset
- CORS configured for `localhost:5173` and `localhost:4173`
- `build_system_prompt(mode, experience_level, workspace_state)` in `services/prompt.py`; system prompt prepended to every `/api/chat` call
- goldilocks-core installed as editable path dependency (`../goldilocks-core`); changes to goldilocks-core are picked up by Python automatically, but **uvicorn `--reload` only watches `app/` — restart uvicorn manually after editing goldilocks-core**

**`/api/dft/kpoints` implementation note:**
Uses `k_distance_to_mesh()` (heuristic, geometry-based), not the ML advisor. The ML advisor (`advise_kpoints`) requires a trained `.joblib` model file which does not exist yet. When a model is available, switch to `advise_kpoints()`.

**`/api/dft/pseudo` implementation note:**
Uses `select_pp_candidates_for_structure()` directly. `advise_pseudos()` in `goldilocks_core.advisors.pp_advisor` is not yet implemented (`raise NotImplementedError`). Requires `PSEUDO_ROOT` env var pointing to the local UPF file directory.

### 3.2 What needs to be built

#### A. System prompt & context injection (high priority)

Currently `/api/chat` passes the raw messages array straight to vLLM with no system prompt. The LLM has no awareness of:
- What mode the user is in
- What DFT code / task / machine is selected
- What structure is attached
- What experience level the user is

**To build:**
- The API should accept extra fields: `mode`, `workspaceState` (code/task/machine/functional/pseudo), `structure` (file name + content), `experienceLevel`
- Build a `build_system_prompt(mode, workspace_state, experience_level)` function that produces a focused system prompt
- Inject the structure content when relevant (truncated if too large)

**Approximate system prompt structure:**
```
You are Goldilocks, an AI assistant for computational materials research at STFC.
Mode: DFT Workspace
User experience: Familiar with workflows

Current workspace:
- Code: Quantum ESPRESSO
- Task: Geometry optimisation
- Functional: PBE
- Pseudopotential: ONCV (SG15)
- Machine: ARCHER2

Structure in chat: Si.cif (2 sites, Si₂, spacegroup Fd-3m)

Respond concisely. For code examples, use QE input file format.
```

#### B. `POST /api/structure-match`

```python
class StructureMatchRequest(BaseModel):
    structure_content: str      # raw file content
    structure_name: str         # filename (used to infer format)
    # OR:
    elements: list[str]         # for formula-based search (no structure file)
    mode: Literal["file", "formula"]
```

```python
class StructureMatchResult(BaseModel):
    formula: str
    spacegroup: str | None
    source: str                 # "Materials Project" | "Materials Cloud" | "NOMAD" | "JARVIS"
    url: str
    matched: bool               # True if StructureMatcher confirmed exact match
    score: float | None         # similarity score if available
```

Implementation:
1. Parse structure using goldilocks-core
2. Extract features: formula, nsites, elements, spacegroup
3. Parallel queries to MP (mp-api), MC OPTIMADE, NOMAD OPTIMADE, JARVIS (local cache)
4. Run `pymatgen.analysis.structure_matcher.StructureMatcher` on candidates
5. Return sorted `StructureMatchResult` list

See `structure-match-design.md` for full OPTIMADE query patterns and link URL templates.

#### C. `POST /api/dft/kpoints`

```python
class KpointsRequest(BaseModel):
    structure_content: str
    structure_name: str
    code: str                   # "quantum-espresso" | "vasp" | "castep" | ...
    task: str                   # "geometry-optimisation" | "band-structure" | ...
```

Calls `goldilocks_core.kpoints.advisor.advise_kpoints()` and returns the recommended k-mesh with a natural-language summary for the LLM.

#### D. `POST /api/dft/pseudo`

Calls `goldilocks_core.pseudo.pp_selector` and returns recommended pseudopotentials for each element in the structure.

#### E. `/api/mlip/*` — forward to janus-api

```
POST /api/mlip/singlepoint   → janus-api /singlepoint
POST /api/mlip/optimise      → janus-api /optimise
POST /api/mlip/md            → janus-api /md
```

#### F. Tool calling / agent loop

Currently the frontend sends messages → vLLM → streams tokens. There is no tool-calling loop. To support autonomous tool use:

1. Use vLLM's OpenAI-compatible tool calling API (non-streaming call first)
2. If `tool_calls` in response → execute the tool (goldilocks-core or janus-api)
3. Append `role: "tool"` message with result
4. Re-send to vLLM → stream final response to frontend

Tools to expose to LLM:
```
predict_kpoints(structure)     → goldilocks-core
select_pseudopotentials(structure, code)  → goldilocks-core
search_databases(structure)    → structure-match route
run_singlepoint(structure, model)  → janus-api
run_optimise(structure, model)     → janus-api
```

Frontend changes needed: show "🔧 tool running..." indicator during tool execution; inject tool result summary into visible messages.

---

## 4. goldilocks-core — Computation Library

### 4.1 What is built

| Module | Contents | Status |
|---|---|---|
| `structure/io.py` | `load_structure()`, `analyze_structure()` | ✅ |
| `kpoints/features.py` | CSLR feature extraction from Structure | ✅ |
| `kpoints/kmesh.py` | K-mesh candidate generation | ✅ |
| `kpoints/advisor.py` | `advise_kpoints()` — full pipeline | ✅ |
| `ml/models.py` | Load joblib model from ModelSpec | ✅ |
| `ml/inference.py` | `predict()` scalar inference | ✅ |
| `pseudo/parse_upf.py` | Parse UPF files (attribute + text style) | ✅ |
| `pseudo/pp_registry.py` | Build registry from local directory scan | ✅ |
| `pseudo/pp_selector.py` | Select best pseudo per element | ✅ |
| `pseudo/pp_policy.py` | Policy rules (functional, type, etc.) | ✅ |
| `pseudo/download.py` | Fetch pseudo libraries | ✅ |
| `shared/types.py` | `StructureAnalysis`, `KMeshEntry`, `KPointsAdvice`, etc. | ✅ |
| CLI `goldilocks-kmesh` | k-mesh recommendation from command line | ✅ |

### 4.2 What needs to be built

#### A. `structure/features.py` — for structure matching

```python
@dataclass
class MatchFeatures:
    formula_reduced: str        # e.g. "Si"
    formula_anonymous: str      # e.g. "A"
    nsites: int
    elements: list[str]
    spacegroup_number: int
    spacegroup_symbol: str

def extract_match_features(structure: Structure) -> MatchFeatures: ...
```

#### B. `structure/match.py` — StructureMatcher wrapper

```python
@dataclass
class MatchResult:
    formula: str
    spacegroup: str | None
    source: str
    url: str
    matched: bool
    score: float | None

def run_matcher(
    query: Structure,
    candidates: list[tuple[Structure, dict]]   # (structure, metadata)
) -> list[MatchResult]: ...
```

Uses `pymatgen.analysis.structure_matcher.StructureMatcher` with `ltol=0.2, stol=0.3, angle_tol=5`.

#### C. API integration layer

Functions that fetch candidates from external databases:
```
structure/sources/mp.py       — Materials Project via mp-api
structure/sources/optimade.py — generic OPTIMADE helper (used by MC + NOMAD)
structure/sources/jarvis.py   — JARVIS local cache load + query
```

These belong in goldilocks-api (network calls), not goldilocks-core (computation only). goldilocks-core provides `extract_match_features()` and `run_matcher()`; goldilocks-api owns all HTTP calls.

#### D. Response summary generation

For sidebar → LLM injection, every API endpoint should return a `summary` field (plain text, ~1–3 sentences) alongside `raw` data. This keeps LLM token usage low.

Example:
```python
def kpoints_summary(advice: KPointsAdvice) -> str:
    return (
        f"Recommended k-grid: {advice.kgrid} (k-spacing {advice.k_distance:.3f} Å⁻¹). "
        f"Reduced k-points: {advice.n_kpoints}."
    )
```

---

## 5. LLM Layer — vLLM / Qwen3

### 5.1 Current state

- vLLM running on STFC cloud VM (`172.16.111.119`)
- Model: Qwen3 (exact version set via `VLLM_MODEL` env var or auto-detected)
- goldilocks-api connects via `VLLM_BASE_URL` env var
- Streaming via `/v1/chat/completions` with `stream: true`
- `<think>...</think>` tokens stripped before forwarding to frontend

### 5.2 What needs to be done

| Item | Priority | Notes |
|---|---|---|
| System prompt injection | High | Mode-aware, includes workspace state and structure |
| Experience-level tone tuning | Medium | "New" users get explanations; "Advanced" gets concise output |
| Tool calling setup | High | vLLM supports OpenAI function-calling format |
| Fine-tuned Goldilocks model | Low (future) | `goldilocks-dft` and `goldilocks-mlip` listed in UI but don't exist yet |
| Context window management | Medium | Long chats may exceed context; implement message truncation strategy |

**System prompt design principle:** short, domain-specific, no generic fluff. The prompt should tell the model its name, its audience (materials scientists), the current mode, and the current workspace state. It should not attempt to explain all of DFT to the model.

---

## 6. janus-api — MLIP Service

### 6.1 Current state

**Does not exist yet.** Referenced in `goldilocks-design.md` and assumed by the MLIP Playground panel.

### 6.2 What needs to be built

A minimal FastAPI wrapper around `janus-core` (STFC's MLIP simulation library):

```
POST /singlepoint   { structure: str, model: str } → { energy, forces, stress, summary }
POST /optimise      { structure: str, model: str } → { final_structure, energy, summary }
POST /md            { structure: str, model: str, steps: int, T: float } → { trajectory, summary }
```

Supported models: MACE-MP-0, CHGNet, ALIGNN (matching the `MLIP_MODELS` list in the frontend).

---

## 7. Deployment

### 7.1 Current deployment

- Frontend: Nginx on STFC cloud VM, serving built Vite assets
- goldilocks-api: uvicorn on STFC cloud VM
- vLLM: separate process on STFC cloud VM
- Configuration: `docker-compose.stfc.yml` + `deploy/stfc-cloud/nginx.conf`

### 7.2 What needs to change as backend grows

- Add JARVIS cache download step to goldilocks-api startup (or a separate init script)
- Add `MP_API_KEY` to `.env` for Materials Project queries
- Add janus-api service to docker-compose when built
- CORS in goldilocks-api: add production domain when deployed publicly

---

## 8. Feature Implementation Order

Priority order based on user-facing impact and dependency chain:

### Phase 1 — Make chat actually useful (no new backend routes) ✅ DONE
1. **System prompt injection** in goldilocks-api `/api/chat` ✅
2. **Frontend sends `mode`, `experience_level`, `workspace_state`** ✅

### Phase 2 — DFT Workspace backend
3. **`POST /api/dft/kpoints`** ✅ — heuristic mesh via `k_distance_to_mesh()`; ML advisor pending model file
4. **`POST /api/dft/pseudo`** ✅ — `select_pp_candidates_for_structure()` with local UPF files
5. **Frontend: DFT Inputs tab** — call `/api/dft/kpoints` + `/api/dft/pseudo` and display results ✗ not done

### Phase 3 — Structure Match
6. **goldilocks-core `structure/features.py`** — `extract_match_features()`
7. **goldilocks-core `structure/match.py`** — `run_matcher()` + `MatchResult`
8. **goldilocks-api `POST /api/structure-match`** — parallel DB queries + StructureMatcher
9. **Frontend: wire "Search databases" button** — replace stub with real API call
10. **Frontend: render dynamic result cards** — replace hardcoded 3 entries

### Phase 4 — MLIP Playground
11. **janus-api** — build FastAPI wrapper (singlepoint + optimise)
12. **goldilocks-api `/api/mlip/*`** — proxy routes to janus-api
13. **Frontend: MLIP metrics / compute tabs** — call real API, render results

### Phase 5 — Agent / tool loop
14. **Tool calling in goldilocks-api** — agent loop with function calling
15. **Frontend: tool execution indicator** — show "🔧 calling goldilocks-core..." while tools run

### Phase 6 — Fine-tuned models (research phase)
16. Train / fine-tune `goldilocks-dft` on domain-specific DFT Q&A data
17. Deploy on vLLM alongside base Qwen3
18. Wire model selector in frontend to actual vLLM model IDs

---

## 9. Known Issues / Limitations

| Issue | Where | Notes |
|---|---|---|
| Structure content truncated to 3000 chars | goldilocks-web `send()` | May lose data for large CIF files |
| `/api/dft/kpoints` uses heuristic, not ML | goldilocks-api | `advise_kpoints()` needs a `.joblib` model file; switch when model is trained |
| `advise_pseudos()` not implemented | goldilocks-core | `raise NotImplementedError`; `/api/dft/pseudo` uses `select_pp_candidates_for_structure()` directly |
| uvicorn `--reload` ignores goldilocks-core changes | goldilocks-api | Restart uvicorn manually after editing goldilocks-core source |
| No JARVIS cache | goldilocks-api | Must be downloaded before structure-match works |
| No MP API key configured | goldilocks-api | `.env.example` exists but key not set |
| Beyond DFT: Setup tab only | goldilocks-web | No Inputs or Checks tabs like DFT Workspace has |
| MLIP all mock | goldilocks-web | Every MLIP number is hardcoded |
| No session persistence across page reload | goldilocks-web | localStorage used, but structure file content lost on reload |
| `janus-api` missing | — | MLIP Playground cannot function until it is built |

---

## 10. File Map

```
code/
├── goldilocks-web/
│   └── src/
│       ├── App.jsx             Main app (~5100 lines; all UI + state)
│       ├── App.css             (empty; all styles are inline in App.jsx)
│       ├── components/
│       │   └── WeasStructureViewport.jsx   3D structure viewer
│       └── utils/
│           └── structureFiles.js           File extension / fence language helpers
│
├── goldilocks-api/
│   └── app/
│       ├── main.py             FastAPI app + CORS
│       ├── routes/
│       │   └── chat.py         POST /api/chat (SSE)
│       └── services/
│           └── llm.py          vLLM client + _filter_think
│
├── goldilocks-core/
│   └── src/goldilocks_core/
│       ├── structure/
│       │   └── io.py           load_structure, analyze_structure
│       ├── kpoints/
│       │   ├── advisor.py      advise_kpoints (full pipeline)
│       │   ├── features.py     CSLR feature extraction
│       │   └── kmesh.py        K-mesh construction
│       ├── pseudo/             UPF parsing + registry + selector
│       ├── ml/                 Model loading + inference
│       └── shared/types.py     Shared dataclasses
│
├── goldilocks-design.md        System-level architecture (EN)
├── goldilocks-design-CN.md     System-level architecture (CN)
├── structure-match-design.md   Structure match feature spec (EN)
├── structure-match-design-CN.md  Structure match feature spec (CN)
├── stfc-cloud-webapp-usage.md  STFC cloud deployment notes (EN)
├── stfc-cloud-webapp-usage-CN.md  (CN)
└── project-status.md           This file
```
