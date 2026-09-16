# goldilocks-agent

LLM orchestration, scientific workflow, and AiiDA execution layer sitting on
top of `goldilocks-core`. **Design-only right now** — see
`docs/goldilocks-agent-design.md` (status line at the top of that file is
authoritative on what exists vs. what's designed but unbuilt).

## Commands

```bash
uv sync --group dev
uv run poe check        # ruff check + format gate + pytest
uv run poe web-check    # frontend: lint + build
uv run pre-commit run --all-files
```

Frontend lives in `app/` (Node/Vite/React, not `uv`) — `cd app && npm install`.
⚠️ Not called `web/`: `goldilocks-core/web/` is a different kind of product
(a form-only tool, no LLM) — see design doc §13.

## Code style

- Ruff `E`, `F`, `I`. Target Python 3.12.
- Domain modules, not generic buckets — no `helpers/`, `utils/`, or `processing/`.
- One clear API; no compatibility shims, legacy aliases, or duplicate import paths unless the user asks for backward compatibility.
- `snake_case` everywhere; no `CamelCase` except string literals matching external formats.
- Docstrings: factual — what it does, returns, assumes. Not essays.

## Tests

- The agent's invariants are mostly "which tools it called with what arguments," not "what it said" — see design doc §7.1 for the three-layer LLM test strategy (stub-LLM determinism layer, classification eval, semantic eval) plus an end-to-end smoke test. Don't assert on free-form LLM output.
- Prioritize scientific/provenance behavior (source tagging, blocked-field handling, draft revisions) over line coverage.

## What doesn't belong here

- **DFT parameter science** (which functional, which k-mesh, why) — that's `goldilocks-core`'s advisors. This package only decides *when to ask* and *how to explain*, never *what the physics answer is*.
- **Model training** — `goldilocks-ml`.
- **Real DFT execution data generation** — `goldilocks-data`.

## Rules

- Use `uv`, not `pip`, for the Python side.
- Never edit or delete GitHub text authored by someone else. PR descriptions are written by a human, always.
- Follow the same coordination-layer / issue-hygiene conventions as the sibling repos (`goldilocks-core`'s `AGENTS.md` is the canonical copy; this file doesn't restate it to avoid a second copy drifting).
