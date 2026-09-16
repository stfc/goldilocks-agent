"""DFT Workspace (design doc 十二 · implementation plan Step 2,
2026-09-15 redone against goldilocks-core's real v2 CLI instead of v1
assumptions, then redone again the same day once goldilocks-core#62
landed -- see that package's own docstring for why CLI, not HTTP/MCP).

Model parsing is tested against real captured JSON (from actually running
`goldilocks explain`/`goldilocks run --json -o <dir>`/`goldilocks
capabilities --json` against the bundled `Si.cif` example, 2026-09-15) --
no mocking of goldilocks-core's own output shape. Actually invoking the
CLI needs a real goldilocks-core checkout with its `http`/`mcp` extras
irrelevant but its base env synced (`uv sync`) -- that's a per-developer
setup step, so the real-subprocess tests are `integration`-marked and
skipped unless `GOLDILOCKS_CORE_PATH` is set (same pattern as
`requires_mlip_enabled` in test_mlip_playground.py).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from goldilocks_agent.tools.dft_workspace import (
    CapabilitiesResult,
    ExplainResult,
    ResolvedField,
    RunResult,
    SettingSpec,
    capabilities,
    explain,
    run,
    run_bundle,
)

requires_core_cli = pytest.mark.skipif(
    not os.environ.get("GOLDILOCKS_CORE_PATH"),
    reason="GOLDILOCKS_CORE_PATH not configured -- DFT Workspace opt-in, see config.py",
)

# Captured live 2026-09-15 from `goldilocks explain
# src/goldilocks_core/examples/structures/Si.cif --hpc scarf --json`
# against the real `4-goldilocks-core` checkout (branch 61-docs-v2-rewrite).
_REAL_EXPLAIN_JSON = {
    "records": {
        "functional": {
            "status": "resolved",
            "value": "PBEsol",
            "source": "heuristic",
            "reason": None,
            "blocked_by": None,
            "field_sources": None,
        },
        "cutoffs": {
            "status": "resolved",
            "value": {"ecutwfc_ry": 48.0, "ecutrho_ry": 192.0, "warnings": []},
            "source": "heuristic",
            "reason": None,
            "blocked_by": None,
            "field_sources": None,
        },
    },
    "warnings": [
        {
            "code": "job.walltime_defaulted",
            "level": "warning",
            "category": "job",
            "message": (
                "walltime not specified; requesting partition 'scarf''s ceiling (168h)."
            ),
        }
    ],
}

_REAL_SETTING_JSON = {
    "key": "functional",
    "group": "functional",
    "type": "string",
    "scope": "system",
    "description": "Exchange-correlation functional label (e.g. PBE, PBEsol).",
    "unit": None,
    "default": "PBEsol",
    "enum": None,
    "enum_from": "pseudopotential_tables.functional",
    "ml_target": None,
    "codes": None,
    "tasks": None,
    "programs": None,
    "approaches": ["human", "heuristic"],
}

# Captured live 2026-09-15 from `goldilocks capabilities --json` against the
# real `4-goldilocks-core` checkout (branch 62-cli-capabilities-command) --
# trimmed to one entry per list, full payload has 49 settings/71
# pseudopotential tables/etc.
_REAL_CAPABILITIES_JSON = {
    "core_version": "0.1.0",
    "vocabulary_version": "1",
    "codes": [
        {
            "id": "quantum_espresso",
            "name": "Quantum ESPRESSO",
            "tasks": ["scf_single_point", "dos", "relax", "vc-relax"],
        }
    ],
    "tasks": [
        {
            "id": "scf_single_point",
            "name": "Single-point SCF",
            "description": "One self-consistent-field calculation, no relaxation.",
            "codes": ["quantum_espresso"],
            "executables": ["pw.x"],
            "step_count": 1,
        }
    ],
    "facts": [
        {
            "key": "is_metal",
            "description": "Whether the structure is metallic, from composition alone.",
            "type": "enum",
            "values": ["metal", "non_metal"],
            "approaches": ["human", "heuristic"],
            "overridable": True,
            "ml_target": "is_metal",
        }
    ],
    "hpc_profiles": [
        {
            "id": "scarf",
            "name": "scarf",
            "scheduler": "slurm",
            "partitions": ["devel", "gpu", "gpu-devel", "preemptable", "scarf"],
        }
    ],
    "models": [],
    "pseudopotential_tables": [
        {
            "id": "pseudodojo-pbesol-efficiency-sr",
            "provider": "pseudodojo",
            "functional": "PBEsol",
            "accuracy": "efficiency",
            "relativistic": "scalar",
            "default": True,
            "elements": ["Si", "Na", "Cl"],
            "citation": "van Setten et al., Comput. Phys. Commun. 226, 39 (2018)",
            "licence": "CC-BY-4.0",
            "version": "0.4",
        }
    ],
    "settings": [_REAL_SETTING_JSON],
    "sources": ["human", "ml", "llm", "heuristic"],
    "warnings": [
        {
            "code": "job.walltime_defaulted",
            "category": "job",
            "level": "warning",
            "message": "walltime not specified; requesting partition's ceiling.",
        }
    ],
}


def test_explain_result_parses_real_captured_json() -> None:
    result = ExplainResult(
        records={
            k: ResolvedField(**v) for k, v in _REAL_EXPLAIN_JSON["records"].items()
        },
        warnings=_REAL_EXPLAIN_JSON["warnings"],
    )

    assert result.records["functional"].value == "PBEsol"
    assert result.records["functional"].source == "heuristic"
    assert result.records["cutoffs"].value["ecutwfc_ry"] == 48.0
    assert result.warnings[0]["code"] == "job.walltime_defaulted"


def test_resolved_field_handles_unavailable_and_blocked_shapes() -> None:
    unavailable = ResolvedField(status="unavailable", reason="no ML model wired")
    blocked = ResolvedField(status="blocked", blocked_by="composition")

    assert unavailable.value is None
    assert blocked.value is None
    assert blocked.blocked_by == "composition"


def test_run_result_holds_only_files_not_records() -> None:
    """2026-09-15 finding: `goldilocks run -o <dir> --json` returns
    `{files, kind, path}`, NOT `{files, records, warnings}` -- that richer
    shape only appears in `run`'s "memory-only preview" mode (no `-o`).
    RunResult must not pretend to carry records/warnings it never gets."""
    result = RunResult(files={"scf.in": "&CONTROL\n..."})

    assert result.files["scf.in"].startswith("&CONTROL")
    assert not hasattr(result, "records")
    assert not hasattr(result, "warnings")


def test_run_result_trims_pseudo_files_for_llm_but_keeps_them_for_panel() -> None:
    result = RunResult(
        files={"scf.in": "&CONTROL\n...", "pseudo/Si.upf": "<UPF version=...>"}
    )

    full = result.model_dump()
    trimmed = result.model_dump_for_llm()

    assert "pseudo/Si.upf" in full["files"]
    assert "pseudo/Si.upf" not in trimmed["files"]
    assert trimmed["files"]["scf.in"] == "&CONTROL\n..."


def test_setting_spec_parses_real_captured_json() -> None:
    spec = SettingSpec(**_REAL_SETTING_JSON)

    assert spec.key == "functional"
    assert spec.default == "PBEsol"
    assert spec.enum_from == "pseudopotential_tables.functional"


def test_capabilities_result_parses_real_captured_json() -> None:
    result = CapabilitiesResult(**_REAL_CAPABILITIES_JSON)

    assert result.codes[0].id == "quantum_espresso"
    assert result.tasks[0].step_count == 1
    assert result.facts[0].key == "is_metal"
    assert result.hpc_profiles[0].scheduler == "slurm"
    assert result.pseudopotential_tables[0].default is True
    assert result.settings[0].key == "functional"
    assert "heuristic" in result.sources
    assert result.warnings[0].code == "job.walltime_defaulted"


@pytest.mark.integration
@requires_core_cli
def test_explain_against_real_core_cli() -> None:
    core_path = os.environ["GOLDILOCKS_CORE_PATH"]
    si_cif = f"{core_path}/src/goldilocks_core/examples/structures/Si.cif"
    with open(si_cif) as f:
        content = f.read()

    result = asyncio.run(
        explain(content, "Si.cif", task="scf_single_point", hpc="scarf")
    )

    assert result.records["cutoffs"].value["ecutwfc_ry"] == 48.0
    assert result.records["functional"].source in {"human", "ml", "llm", "heuristic"}


@pytest.mark.integration
@requires_core_cli
def test_run_against_real_core_cli_generates_real_files() -> None:
    core_path = os.environ["GOLDILOCKS_CORE_PATH"]
    si_cif = f"{core_path}/src/goldilocks_core/examples/structures/Si.cif"
    with open(si_cif) as f:
        content = f.read()

    result = asyncio.run(run(content, "Si.cif", task="scf_single_point", hpc="scarf"))

    assert "scf.in" in result.files
    assert "calculation" in result.files["scf.in"]
    # 2026-09-15, second pass: pseudo is no longer excluded -- the Inputs
    # tab lists it alongside the input/submission-script files now.
    assert any(name.startswith("pseudo/") for name in result.files)


@pytest.mark.integration
@requires_core_cli
def test_capabilities_against_real_core_cli() -> None:
    result = asyncio.run(capabilities())

    assert len(result.settings) == 49
    assert any(s.key == "functional" for s in result.settings)
    assert any(c.id == "quantum_espresso" for c in result.codes)
    assert any(p.id == "scarf" for p in result.hpc_profiles)
    assert result.pseudopotential_tables  # real dropdown data, not a hardcoded guess


@pytest.mark.integration
@requires_core_cli
def test_run_bundle_against_real_core_cli_produces_a_real_zip() -> None:
    import zipfile
    from io import BytesIO

    core_path = os.environ["GOLDILOCKS_CORE_PATH"]
    si_cif = f"{core_path}/src/goldilocks_core/examples/structures/Si.cif"
    with open(si_cif) as f:
        content = f.read()

    zip_bytes = asyncio.run(
        run_bundle(content, "Si.cif", task="scf_single_point", hpc="scarf")
    )

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "scf.in" in names
        assert "submit.sh" in names
        assert any(name.startswith("pseudo/") for name in names)
