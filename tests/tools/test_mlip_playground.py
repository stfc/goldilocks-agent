"""MLIP Playground (design doc 12.4 · implementation plan Step 7,
2026-09-15 confirmed as the next piece to build; same-day rewrite from
vendored-janus-api/HTTP to a direct `janus` CLI subprocess, see
`client.py`'s docstring).

Model shape/trimming is tested directly (pure Python, no network or
subprocess needed). Actually running a calculation needs `./mlip-cli`
synced (first `janus` invocation pays `uv sync`'s own download cost,
several GB for torch/mace) -- that's a per-developer setup step, not
something CI or a fresh clone has, so the real run is `integration`-marked
and skipped unless `GOLDILOCKS_AGENT_MLIP_ENABLED` is set (same pattern as
`requires_anthropic_key`/`requires_local_model` in test_graph.py).
AGENTS.md's testing philosophy: verify real behavior when it's available,
don't mock the CLI's own file-writing contract just to have *a* test for
it.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from goldilocks_agent.tools.mlip_playground import (
    EosResult,
    GeomOptResult,
    NebResult,
    PhononsResult,
    SinglePointResult,
    run_geometry_optimization,
    run_singlepoint,
)

requires_mlip_enabled = pytest.mark.skipif(
    not os.environ.get("GOLDILOCKS_AGENT_MLIP_ENABLED"),
    reason="GOLDILOCKS_AGENT_MLIP_ENABLED not set -- MLIP Playground opt-in",
)

_NACL_CIF = """\
data_NaCl
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M "P 1"
loop_
_atom_site_label
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na 0.0 0.0 0.0
Cl 0.5 0.5 0.5
"""


def test_singlepoint_result_trims_forces_for_llm_but_keeps_them_for_panel() -> None:
    result = SinglePointResult(energy=-3.5, forces=[[0.1, 0.0, 0.0]], stress=[0.01])

    full = result.model_dump()
    trimmed = result.model_dump_for_llm()

    assert full["forces"] == [[0.1, 0.0, 0.0]]
    assert "forces" not in trimmed
    assert trimmed["energy"] == -3.5
    assert "eV" in result.summary()


def test_geomopt_result_trims_optimised_structure_for_llm() -> None:
    result = GeomOptResult(
        final_energy=-10.0, max_force=0.02, optimised_structure=_NACL_CIF
    )

    trimmed = result.model_dump_for_llm()

    assert "optimised_structure" not in trimmed
    assert result.model_dump()["optimised_structure"] == _NACL_CIF
    assert "final energy" in result.summary()


def test_eos_result_trims_arrays_and_svg_for_llm() -> None:
    result = EosResult(
        bulk_modulus=24.5,
        v_0=45.0,
        e_0=-3.2,
        volumes=[40.0, 45.0, 50.0],
        energies=[-3.0, -3.2, -3.1],
        eos_svg="<svg></svg>",
    )

    trimmed = result.model_dump_for_llm()

    assert set(trimmed) == {"bulk_modulus", "v_0", "e_0"}
    assert "bulk modulus" in result.summary()


def test_neb_result_trims_svg_and_traj_for_llm() -> None:
    result = NebResult(
        barrier=0.5, delta_e=0.1, max_force=0.05, neb_svg="<svg/>", neb_traj="...xyz..."
    )

    trimmed = result.model_dump_for_llm()

    assert set(trimmed) == {"barrier", "delta_e", "max_force"}
    assert "barrier" in result.summary()


def test_phonons_result_trims_band_svg_and_yaml_for_llm() -> None:
    result = PhononsResult(
        temperatures=[0.0, 300.0],
        heat_capacity=[0.0, 12.3],
        entropy=[0.0, 5.0],
        free_energy=[1.0, 0.5],
        band_svg="<svg/>",
        band_yaml="phonopy: {}",
    )

    trimmed = result.model_dump_for_llm()

    assert "band_svg" not in trimmed
    assert "band_yaml" not in trimmed
    assert trimmed["temperatures"] == [0.0, 300.0]
    assert "2 temperature points" in result.summary()


def test_result_summaries_degrade_gracefully_with_no_data() -> None:
    assert "no energy" in SinglePointResult().summary()
    assert "no final energy" in GeomOptResult().summary()
    assert "no bulk modulus" in EosResult().summary()
    assert "no barrier" in NebResult().summary()
    assert "no thermal properties" in PhononsResult().summary()


@pytest.mark.integration
@requires_mlip_enabled
def test_run_singlepoint_against_real_janus_cli() -> None:
    result = asyncio.run(run_singlepoint(_NACL_CIF, "NaCl.cif"))
    assert result.energy is not None


@pytest.mark.integration
@requires_mlip_enabled
def test_run_geomopt_against_real_janus_cli() -> None:
    result = asyncio.run(
        run_geometry_optimization(_NACL_CIF, "NaCl.cif", fmax=0.2, steps=50)
    )
    assert result.final_energy is not None
    assert result.optimised_structure
