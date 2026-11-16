"""What the chat's DFT Workbench tools tell the model about a draft, on the
server (#142). Ported from `app/src/dftWorkbench.ts`.

The model gets core's values and the source that decided each, never the
several kB of provenance the Workbench's cards show (model evidence,
citations, per-field sources, layout), which its context doesn't need.
"""

from __future__ import annotations

import json

from goldilocks_agent.core_api import CoreFailure
from goldilocks_agent.dft_draft import DftDraft


def calculation_of(draft: DftDraft) -> dict:
    """The draft's calculation: code, task, HPC profile and overrides."""
    return {
        "code": draft.code,
        "task": draft.task,
        "hpc": draft.hpc,
        "overrides": draft.overrides,
    }


def structure_summary(inspection: dict) -> dict:
    """What the model needs of an inspection: the composition and cell, and
    how many sites hold partial occupancies -- core refuses to recommend for
    a structure with any (goldilocks-core fork #229). Leaves out the file
    and the per-site coordinates."""
    structure = inspection["structure"]
    lattice = structure["lattice"]
    return {
        "formula": structure["formula"],
        "reduced_formula": structure["reduced_formula"],
        "site_count": structure["site_count"],
        "periodicity": structure["periodicity"],
        "lattice": {
            key: lattice[key]
            for key in ("lengths_angstrom", "angles_degrees", "volume_angstrom3")
        },
        "partially_occupied_sites": sum(
            1
            for site in structure["sites"]
            if len(site["species"]) > 1
            or any(species["occupancy"] < 1 for species in site["species"])
        ),
    }


def _record_for_model(record: dict) -> dict:
    if record["status"] == "resolved":
        return {
            "value": record.get("value"),
            "source": record.get("source"),
            **(
                {"source_note": record["source_note"]}
                if record.get("source_note")
                else {}
            ),
        }
    return {
        "status": record["status"],
        **({"reason": record["reason"]} if record.get("reason") else {}),
        **({"blocked_by": record["blocked_by"]} if record.get("blocked_by") else {}),
    }


def review_for_model(draft: DftDraft, explain: dict) -> dict:
    """A review for the model: each record's value, the tier that decided it
    and the advisor's note where there is one (e.g. why mMACE wasn't run) --
    or, unresolved, why not -- with the warnings and the findings that block
    generating input files, each with its remedies."""
    return {
        "calculation": calculation_of(draft),
        "records": {
            name: _record_for_model(record)
            for name, record in explain["records"].items()
        },
        "warnings": [
            {key: warning.get(key) for key in ("record", "level", "message")}
            for warning in explain.get("warnings", [])
        ],
        "blocking": [
            {key: finding.get(key) for key in ("summary", "fields", "remedies")}
            for finding in explain.get("blocking", [])
        ],
    }


def findings_text(findings: list[dict]) -> str:
    """Blocking findings, each with the override patches that resolve it,
    for an error the model reads."""
    parts = []
    for finding in findings:
        remedies = " or ".join(json.dumps(r) for r in finding.get("remedies") or [])
        parts.append(
            f"{finding['summary']} (resolved by setting {remedies})"
            if remedies
            else finding["summary"]
        )
    return " ".join(parts)


def failure_text(failure: CoreFailure) -> str:
    """Why a core request failed, for the model: the blocking findings it
    carries, with their remedies, else core's kind and message. Core answers
    500 when it fails internally (the same request then fails the same way)
    and 424 when its models or pseudopotentials are missing or corrupt."""
    findings = [
        f
        for f in failure.details.get("findings") or []
        if isinstance(f, dict) and isinstance(f.get("summary"), str)
    ]
    if findings:
        return f"{failure.kind}: {findings_text(findings)}"
    hint = ""
    if failure.status == 500:
        hint = (
            " (goldilocks-core failed internally; retrying the same request won't "
            "help, and the agent's server log has core's traceback)"
        )
    elif failure.status == 424:
        hint = (
            " (goldilocks-core's models or pseudopotentials are missing or corrupt; "
            "`goldilocks assets install workbench` reinstalls them, and "
            "goldilocks-agent runs it whenever it starts core)"
        )
    return f"{failure.kind}: {failure.message}{hint}"
