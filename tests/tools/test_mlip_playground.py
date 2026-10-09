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

from goldilocks_agent.tools import (
    CONFIRMATION_REQUIRED_TOOLS,
    MLIP_PLAYGROUND_RELEASED,
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
)
from goldilocks_agent.tools.mlip_playground import (
    EosResult,
    GeomOptResult,
    NebResult,
    PhononsResult,
    SinglePointResult,
    run_equation_of_state,
    run_geometry_optimization,
    run_neb,
    run_phonons,
    run_singlepoint,
)
from goldilocks_agent.tools.mlip_playground.tool import (
    TOOL_DISPATCH as MLIP_TOOL_DISPATCH,
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

# Smallest realistic NEB case: nearest-neighbour vacancy hop in a 2x1x1 FCC
# Al supercell (7 atoms -- 8-atom conventional cubic cell minus one vacancy).
# Both endpoints have the same 7 atoms; only the vacancy site differs (atom
# at (0, 0.5, 0.5) in the init structure has hopped to the vacant (0, 0, 0)
# site in the final structure, the FCC nearest-neighbour distance, ~2.86 Å
# for a=4.05 Å). Verified live against the real `janus neb` CLI before
# adding here: converges in ~7s with `--n-images 3 --fmax 0.5`, "ase"/idpp
# interpolation (janus-core's own default) avoids clashing with neighbouring
# atoms along the path, and gives a barrier (~0.76 eV) in the right
# ballpark for known Al vacancy-migration energies (~0.6-0.7 eV,
# experiment/DFT) -- plausible, not exact, since this is an unrelaxed,
# single-vacancy, non-cubic-shaped small supercell, not a converged
# calculation.
_AL_VACANCY_INIT_CIF = """\
data_image0
_chemical_formula_structural       Al7
_chemical_formula_sum              "Al7"
_cell_length_a       8.1
_cell_length_b       4.05
_cell_length_c       4.05
_cell_angle_alpha    90.0
_cell_angle_beta     90.0
_cell_angle_gamma    90.0

_space_group_name_H-M_alt    "P 1"
_space_group_IT_number       1

loop_
  _space_group_symop_operation_xyz
  'x, y, z'

loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_fract_x
  _atom_site_fract_y
  _atom_site_fract_z
  _atom_site_occupancy
  Al  Al1       1.0  0.0  0.5  0.5  1.0000
  Al  Al2       1.0  0.25  0.0  0.5  1.0000
  Al  Al3       1.0  0.25  0.5  0.0  1.0000
  Al  Al4       1.0  0.5  0.0  0.0  1.0000
  Al  Al5       1.0  0.5  0.5  0.5  1.0000
  Al  Al6       1.0  0.75  0.0  0.5  1.0000
  Al  Al7       1.0  0.75  0.5  0.0  1.0000
"""

_AL_VACANCY_FINAL_CIF = """\
data_image0
_chemical_formula_structural       Al7
_chemical_formula_sum              "Al7"
_cell_length_a       8.1
_cell_length_b       4.05
_cell_length_c       4.05
_cell_angle_alpha    90.0
_cell_angle_beta     90.0
_cell_angle_gamma    90.0

_space_group_name_H-M_alt    "P 1"
_space_group_IT_number       1

loop_
  _space_group_symop_operation_xyz
  'x, y, z'

loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_fract_x
  _atom_site_fract_y
  _atom_site_fract_z
  _atom_site_occupancy
  Al  Al1       1.0  0.0  0.0  0.0  1.0000
  Al  Al2       1.0  0.25  0.0  0.5  1.0000
  Al  Al3       1.0  0.25  0.5  0.0  1.0000
  Al  Al4       1.0  0.5  0.0  0.0  1.0000
  Al  Al5       1.0  0.5  0.5  0.5  1.0000
  Al  Al6       1.0  0.75  0.0  0.5  1.0000
  Al  Al7       1.0  0.75  0.5  0.0  1.0000
"""


def test_llm_is_offered_mlip_tools_only_once_released() -> None:
    """While MLIP Playground is "Coming soon" its panel can't open, so none
    of its tools may reach the LLM's tool list, dispatch table, or
    confirmation gate -- and all of them must come back once released."""
    mlip_names = set(MLIP_TOOL_DISPATCH)
    offered = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    expected = mlip_names if MLIP_PLAYGROUND_RELEASED else set()
    assert offered & mlip_names == expected
    assert set(TOOL_DISPATCH) & mlip_names == expected
    assert CONFIRMATION_REQUIRED_TOOLS & mlip_names == expected


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


@pytest.mark.integration
@requires_mlip_enabled
def test_run_eos_against_real_janus_cli() -> None:
    result = asyncio.run(run_equation_of_state(_NACL_CIF, "NaCl.cif"))
    assert result.bulk_modulus is not None
    assert result.bulk_modulus > 0
    assert result.v_0 is not None
    assert result.e_0 is not None
    assert result.volumes and result.energies
    assert len(result.volumes) == len(result.energies) == 7
    assert result.eos_svg and "<svg" in result.eos_svg


@pytest.mark.integration
@requires_mlip_enabled
def test_run_neb_against_real_janus_cli() -> None:
    """First-ever real run of `run_neb` (previously only checked against
    source, never executed) -- an Al FCC nearest-neighbour vacancy hop, the
    smallest structure that still makes a legitimate two-endpoint NEB case
    (see `_AL_VACANCY_INIT_CIF`'s comment for why this structure)."""
    result = asyncio.run(
        run_neb(
            _AL_VACANCY_INIT_CIF,
            "Al_vacancy_init.cif",
            _AL_VACANCY_FINAL_CIF,
            "Al_vacancy_final.cif",
            n_images=3,
            fmax=0.5,
        )
    )
    assert result.barrier is not None
    assert result.barrier > 0
    assert result.delta_e is not None
    assert result.max_force is not None
    assert result.neb_svg and "<svg" in result.neb_svg
    assert result.neb_traj and "Lattice" in result.neb_traj


@pytest.mark.integration
@requires_mlip_enabled
def test_run_phonons_against_real_janus_cli() -> None:
    result = asyncio.run(run_phonons(_NACL_CIF, "NaCl.cif"))
    assert result.temperatures
    assert result.heat_capacity and len(result.heat_capacity) == len(
        result.temperatures
    )
    assert result.entropy and result.free_energy
    # heat capacity must rise from 0 at T=0 towards the Dulong-Petit limit --
    # a real physical constraint, not just "some numbers came back".
    assert result.heat_capacity[0] == pytest.approx(0.0, abs=1e-6)
    assert result.heat_capacity[-1] > result.heat_capacity[0]
    assert result.band_svg and "<svg" in result.band_svg
    # band_yaml is best-effort (render_band_yaml.py's own phonopy
    # post-processing subprocess, see client.py's docstring) -- assert it
    # actually succeeded here rather than silently accepting None, since a
    # real run is exactly what should catch that step regressing. "nqpoint"
    # is phonopy's own band.yaml top-level key (verified against a real
    # render_band_yaml.py run), not something we're inventing here.
    assert result.band_yaml and "nqpoint" in result.band_yaml
