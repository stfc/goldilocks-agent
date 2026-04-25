# Goldilocks Design Document

*Last updated: 2026-04-25 (session 3)*

## Overview

Goldilocks is a web app for computational materials research at STFC. The primary interface is a chat-first LLM assistant. Users can also activate specialised workspace modes via a right sidebar; the sidebar state is shared with the LLM automatically.

---

## Project Status

| Layer | Component | Status |
|---|---|---|
| Frontend | Full UI — chat, 4 modes, sidebar, all workspace panels | ✅ Built |
| Frontend | i18n (en / fr / de / it / zh) | ✅ Built |
| Frontend | Structure viewer (WEAS 3D) | ✅ Built |
| Frontend | Periodic table popup | ✅ Built |
| Frontend | Find in Databases panel (mock data) | ✅ Built (mock) |
| Frontend | DFT Workspace panel — all pickers | ✅ Built |
| Frontend | MLIP Playground panel (UI only) | ✅ Built (UI only) |
| Frontend | Let's Go Cutting-Edge panel (UI only) | ✅ Built (UI only) |
| API | `/api/chat` — LLM proxy, SSE streaming | ✅ Built |
| API | `/api/health` | ✅ Built |
| API | `/api/structure-match` — real DB search | ✗ Planned |
| API | `/api/dft/kpoints` — k-mesh recommendation | ✗ Planned |
| API | `/api/dft/pseudo` — pseudopotential selection | ✗ Planned |
| API | `/api/mlip/*` — MACE / CHGNet calculations | ✗ Planned |
| API | Agent loop (system prompt + tool calling) | ✗ Planned |
| Core | `goldilocks-core` library | ✗ Planned |
| Core | `janus-api` service | ✗ Planned |
| Infra | vLLM (Qwen3) on STFC cloud | ✅ Running |

---

## Package Structure

| Package | Type | Responsibility |
|---|---|---|
| `goldilocks-web` | React/Vite app | Frontend UI |
| `goldilocks-api` | FastAPI service | Orchestration layer, sole HTTP entry point |
| `goldilocks-core` | Python library | DFT parameter prediction, lightweight calculations, ML models |
| `janus-api` | FastAPI service | HTTP wrapper around janus-core (not yet built) |
| `janus-core` | Python library | MLIP simulations (MACE, CHGNet, etc.) |
| vLLM | External service | Qwen3 inference, hosted on STFC cloud |

`goldilocks-core` is imported directly into `goldilocks-api` (no separate service — calculations are lightweight).

`janus-api` is a general-purpose MLIP HTTP interface, not Goldilocks-specific. Other projects can use it independently.

---

## Frontend — Mode System

The app organises features into four **modes**. Activating a mode opens the right sidebar panel for that mode and scopes the chat to the relevant domain.

| Mode | Colour | Right sidebar | Backend |
|---|---|---|---|
| Find in Databases | Purple | Structure search panel | goldilocks-api `/api/structure-match` |
| MLIP Playground | Green | Model selector + analysis tabs | janus-api (via goldilocks-api) |
| DFT Workspace | Amber | Task builder panel | goldilocks-api `/api/dft/*` |
| Let's Go Cutting-Edge | Blue | Method selector | goldilocks-api (planned) |

Modes are selected from a launcher row below the chat input, or from the mode icon in the header. Activating a mode injects a `[Mode: ...]` prefix into the next user message (until proper system-prompt injection is implemented).

---

## Frontend — UI Structure

```
┌─ Sidebar (260px) ─────────────────────────────────────────────────────────┐
│  Brand logo + name                                                         │
│  ─── Sessions ────────────────────────────────────────────────────────── │
│  • Session list (recent chats, project-grouped chats)                      │
│  • Projects section                                                        │
└───────────────────────────────────────────────────────────────────────────┘

┌─ Main area ───────────────────────────────┬─ Right panel (320px) ─────────┐
│  ┌─ Header ────────────────────────────┐  │  Mode header + powered-by     │
│  │  Mode badge · Model selector · Theme│  │  ─────────────────────────── │
│  └─────────────────────────────────────┘  │  Mode-specific content        │
│                                           │  (see per-mode sections below)│
│  ┌─ Chat transcript ───────────────────┐  │                               │
│  │  User messages (with file badges)   │  │                               │
│  │  Assistant messages (markdown)      │  │                               │
│  └─────────────────────────────────────┘  │                               │
│                                           │                               │
│  ┌─ Composer ──────────────────────────┐  │                               │
│  │  Tools row: [📎 clip] [🔭 table]   │  │                               │
│  │             [periodic table] [viewer]│  │                               │
│  │  Mode launcher: 4 mode chips        │  │                               │
│  │  Text input (auto-resize)           │  │                               │
│  │  Send / Stop button                 │  │                               │
│  └─────────────────────────────────────┘  │                               │
└───────────────────────────────────────────┴───────────────────────────────┘
```

### Structures in Current Chat

Lists all structure files in the active session:
- Files attached but not yet sent (marked "Pending")
- Files embedded in sent messages (extracted from message content)

This list is shared between: Structure Viewer (3D display), Find in Databases panel (query structure selection), and DFT Workspace panel (structure field).

### Structure Viewer

WEAS-based 3D viewer embedded in the composer tools row. Displays the currently selected structure from "Structures in Current Chat". Navigation arrows let the user switch between multiple structures in the same session.

### Periodic Table

Popup launched from the composer tools row. Two functions:
- **Browse** — click elements to highlight them in the chat input
- **Formula builder** — click elements to accumulate a chemical formula (Fe → Fe₂ → Fe₃). Buttons: ↵ insert into input · Search (trigger structure match) · ✕ clear

---

## Frontend — DFT Workspace Panel

Powered by goldilocks-core. Three tabs: **Setup**, **Inputs**, **Checks**.

### Setup tab — picker layout

```
Structure       <filename or "No structure in chat">    (read-only)
Machine         ARCHER2                                       ▼
Code            Quantum ESPRESSO                    ✦ Ask     ▼
Task            Geometry optimisation               ✦ Ask     ▼
────────────────────────────────────────────────────────────────
Task builder              ✦ Ask Goldilocks
────────────────────────────────────────────────────────────────
Functional      PBE                                           ▼
Pseudopotential ONCV                                          ▼
K-mesh method   Monkhorst-Pack                      ✦ Ask     ▼
K-distance      Standard (~0.25 Å⁻¹)               ✦ Ask     ▼  ← disabled when kplib
Smearing method Methfessel-Paxton N=1                         ▼
Smearing width  0.02 eV                                       ▼
```

**✦ Ask pill** — amber pill button on picker triggers that have a recommended item (Code, Task, K-mesh method, K-distance). Clicking auto-selects the recommended value and inserts a warm conversational prompt into the chat input, e.g. "I just picked Quantum ESPRESSO — what makes it a great choice, and what's it really good at?"

**Task builder ✦ Ask Goldilocks** — amber pill on the Task builder header. Auto-selects Functional=PBE + Pseudo=ONCV + K-distance=Standard + Smearing=MP1 at 0.02 eV; inserts a combined explanation prompt.

**Machine has no ✦** — users can only run on machines they have access to; a recommendation would not be actionable.

**✦ star in dropdown options** — every picker option row shows a small amber ✦ circle on hover. Clicking inserts a conversational prompt about that specific option into the chat input.

### Picker option groups

| Picker | Groups |
|---|---|
| Machine | General UK national and Tier-2 systems · Accelerated and specialist UK systems |
| Code | Periodic solid-state workhorses · High-accuracy and linear-scaling · All-electron and specialist · Molecular and mixed workflows |
| Task | Ground-state and relaxation · Electronic structure · Vibrations and response · Defects, surfaces, and transport |
| Functional | LDA · GGA · Meta-GGA · Hybrid |
| Pseudopotential | Pseudopotential types · Libraries |
| K-mesh method | Grid type (Monkhorst-Pack / Γ-centred) · Automatic k-path (kplib) |
| K-distance | Spacing (Γ-only / Light / Standard / Dense / Very dense) — disabled when K-mesh method = kplib |
| Smearing method | For metals (MP1 / MP2 / Marzari-Vanderbilt / Fermi-Dirac) · For insulators (Gaussian / Tetrahedron) |
| Smearing width | 0.005 / 0.01 / 0.02 / 0.05 / 0.1 / 0.2 eV |

### Inputs tab

Preview of the DFT guidance context that will be sent to the LLM (code, task, machine summary). Intended to eventually show a real generated input file skeleton from goldilocks-core.

### Checks tab

Checklist of validation reminders derived from the current picker state (code consistency, k-points, smearing, parallelisation fit).

---

## Frontend — Let's Go Cutting-Edge Panel

Powered by goldilocks-core (planned). Setup tab only (no Inputs or Checks yet).

Pickers: Method · Code · Machine.

Method groups: Correlated and corrected DFT (DFT+U, DFT+DMFT, Hybrid, SIC) · Many-body perturbation theory (GW, BSE, RPA, MP2) · Time-dependent (TDDFT) · Quantum Monte Carlo (QMC) · Model and embedding (MFT, Wannier, QM/MM).

---

## Frontend — Find in Databases Panel

Powered by goldilocks-api. No tabs.

**Query box** — single card with a **Structure | Formula** toggle at the top:
- **Structure mode**: shows the selected structure from "Structures in Current Chat" (navigation arrows when multiple files present); Search button disabled when no structure is loaded.
- **Formula mode**: free-text input (e.g. `Fe2O3`); Search button disabled when input is empty.

**What are you looking for?** — multi-select property chips below the query box. Used to filter sub-datasets per database. Options: Electronic structure · Stability & energy · Magnetic · Elastic & mechanical · Phonons & thermal · Optical.

**Candidate structures section** — results grouped by database, each group collapsible. Each entry shows: formula (bold) · space group · clickable link. Header has a **✦ Ask Goldilocks** pill to discuss the results with the LLM.

Two search modes:

| Mode | Trigger | Filter | StructureMatcher |
|---|---|---|---|
| File-based | "Search databases" button (Structure mode) | Formula + nsites + elements | Yes |
| Formula-based | "Search databases" button (Formula mode) | Elements only | No |

### Database access

| Database | Access method | Auth | Result link format |
|---|---|---|---|
| Materials Project | `mp-api` Python library (server-side) | API key stored in goldilocks-api `.env` — users never see it | `https://next-gen.materialsproject.org/materials/{mp-id}` |
| JARVIS | `jarvis-tools` local dataset (downloaded once, cached) | None | `https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{JVASP-ID}.xml` (public, no login) |
| Materials Cloud | OPTIMADE REST API (`optimade.materialscloud.org`) | None | `https://mc3d.materialscloud.org/details/{mc3d-id}/pbesol-v2` |
| NOMAD | NOMAD API / OPTIMADE | None | NOMAD entry URL |

---

## Frontend — MLIP Playground Panel

Powered by janus-core via janus-api (not yet connected). Model selector at top (MACE-MP-0, CHGNet, ALIGNN). Three tabs: Analysis · Metrics · Compute.

---

## API Structure (goldilocks-api)

```
goldilocks-api
  ├── /api/chat              LLM proxy (SSE streaming)       ✅ built
  ├── /api/health            Status check                    ✅ built
  ├── /api/structure-match   Structure search across DBs     ✗ planned
  ├── /api/dft/
  │     ├── /kpoints         K-mesh recommendation           ✗ planned
  │     └── /pseudo          Pseudopotential selection        ✗ planned
  └── /api/mlip/
        ├── /singlepoint     Single-point via janus-api      ✗ planned
        ├── /optimise        Structure optimisation          ✗ planned
        └── /md              Molecular dynamics              ✗ planned
```

`/api/dft/*` calls `goldilocks-core` directly (in-process).
`/api/mlip/*` forwards to `janus-api`.

---

## Agent Loop (/api/chat — planned)

Current state: `/api/chat` proxies messages straight to vLLM with no system prompt and no tool calling.

Target state:

```
Frontend sends: messages + mode + workspaceState + structure + experienceLevel
  → goldilocks-api builds system prompt + tool schemas, sends to vLLM
  → vLLM returns tool_call (non-streaming)
  → goldilocks-api executes tool (goldilocks-core or janus-api)
  → tool result sent back to vLLM as tool message
  → vLLM streams final natural-language response
  → SSE returned to frontend
```

Tools to expose to LLM:
- `predict_kpoints(structure)` → goldilocks-core
- `select_pseudopotentials(structure, code)` → goldilocks-core
- `search_databases(structure)` → `/api/structure-match`
- `run_singlepoint(structure, model)` → janus-api
- `run_optimise(structure, model)` → janus-api

---

## Sidebar → Chat Integration

When a user runs a calculation directly in the sidebar:

1. `goldilocks-api` returns both `raw` and `summary` fields for every calculation endpoint
2. Frontend uses `raw` to render results in the sidebar
3. Frontend **automatically** appends `summary` as a system message to the conversation history
4. Next LLM call includes the sidebar result in context — no user action required

```json
{
  "raw": { "energy": -3.42, "forces": [[...], ...] },
  "summary": "MACE single-point complete: energy -3.42 eV, max force 0.02 eV/Å, structure stable"
}
```

---

## Shared Frontend State

```
session
  ├── messages: Message[]           Full conversation history
  ├── mode: string | null           Active mode id
  ├── rightPanelOpen: bool
  └── rightPanelView: string | null Tab within the right panel

attachedFile                        Pending structure file (pre-send)
chatStructures                      All structure files extracted from message history
viewerIdx                           Which structure is shown in viewer / used for match query

selectedDftCode                     Code picker value
selectedDftFunctional               Functional picker value
selectedDftPseudo                   Pseudopotential picker value
selectedDftTask                     Task picker value
selectedDftMachine                  Machine picker value
selectedDftKmethod                  K-mesh method picker value (Monkhorst-Pack / Γ-centred / kplib)
selectedDftKdistance                K-distance picker value — ignored when kmethod = kplib
selectedDftSmearingMethod           Smearing method picker value
selectedDftSmearingWidth            Smearing width picker value
selectedBeyondDftMethod             Let's Go Cutting-Edge method picker value
selectedMlipModel                   MLIP model selection

input                               Chat input box content
loading                             SSE stream active
structureMatchLoading               Structure match in progress

language                            UI language (en / fr / de / it / zh), persisted to localStorage
```

---

## Data Flow Summary

```
goldilocks-web
      │ HTTPS / SSE
      ▼
goldilocks-api          ← lightweight orchestration
   ├── import goldilocks-core    (DFT, in-process)
   ├── → vLLM                   (LLM inference)
   └── → janus-api              (MLIP)
              └── import janus-core
```

---

## Decisions Log

| Decision | Choice | Reason |
|---|---|---|
| Frontend framework | React + Vite (not Next.js) | SSR not needed for this use case |
| Tool results in chat | Plain text (LLM describes) | Simpler, more natural conversation |
| Sidebar → chat injection | Automatic, inject summary only | Low effort UX, token count stays low |
| goldilocks-core deployment | In-process (imported by goldilocks-api) | Calculations are lightweight, no separate service needed |
| janus-api scope | General-purpose, not Goldilocks-specific | Reusable by other projects |
| Structure data format | CIF / POSCAR / XYZ upload, ASE-compatible JSON internally | Standard formats for materials science |
| Mode names | Find in Databases · MLIP Playground · DFT Workspace · Let's Go Cutting-Edge | Descriptive + energetic for the cutting-edge mode |
| Machine picker has no ✦ recommend | Users can only run on machines they have access to | Recommendation not actionable without knowing user's access |
| Task builder ✦ scope | Recommends Functional + Pseudo + K-mesh + Smearing together | These four are tightly coupled; recommending them as a set avoids partial inconsistency |
| Code and Task outside Task builder | Code and Task each have their own ✦ | Code choice is upstream of everything; Task choice is orthogonal to the functional/pseudo/mesh/smearing block |
| UI i18n | TRANSLATIONS constant + `t(key)` function, language stored in localStorage | Simple flat-key lookup; scientific/technical terms (PBE, ONCV, MACE, etc.) kept in English across all locales |
| Languages supported | English · Français · Deutsch · Italiano · 中文 | Coverage of main user communities at STFC and European partner institutions |
| Materials Project auth | API key in goldilocks-api `.env`, never exposed to frontend | MP terms allow serving multiple users with one key; users need no account |
| JARVIS data access | `jarvis-tools` downloads full dataset locally; result links point to public XML files | XML at `ctcms.nist.gov` is publicly accessible with no login; avoids dependency on JARVIS website auth |
| Materials Cloud links | `mc3d.materialscloud.org/details/{id}/pbesol-v2` format | Direct entry URL; the old `mc3d.materialscloud.org/{id}` format does not resolve |
| K-mesh split into method + distance | Two separate pickers: K-mesh method (grid type / kplib) and K-distance (spacing preset) | kplib generates its own k-path and does not accept a k-distance; a single picker cannot represent both concepts cleanly |
| ✦ Ask replaces ? buttons | All ask-buttons (picker trigger pills and dropdown option circles) use the ✦ amber star | Consistent amber star language across the whole UI; warmer conversational prompts replace the cold "What should I know about X?" phrasing |
| Settings button simplified | Shows only "Settings" text, centered; no icon or subtitle | Reduces visual weight in the sidebar footer |

---

## Still To Decide

- Storage: persist uploaded structures and calculation results (filesystem + SQLite vs stateless session-only)?
- Agent loop streaming UX: what does the frontend show during tool execution?
- Beyond DFT / Let's Go Cutting-Edge: add Inputs and Checks tabs to match DFT Workspace?
- Formula-based search result display: how to visually distinguish from file-based search results?
