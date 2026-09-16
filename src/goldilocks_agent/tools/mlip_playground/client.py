"""Shells out to janus-core's own `janus` CLI (isolated in `./mlip-cli`,
see `config.py`'s `mlip_cli_path()`/`read_mlip_enabled()`) -- replacing the
earlier vendored-`janus-api`/HTTP-subprocess design (2026-09-15
architecture change: no more `janus-api`, no persistent service/port).

Unlike goldilocks-core's own CLI, `janus` has no `--json`/structured-stdout
mode -- every subcommand writes plain-text/extxyz/YAML/SVG result files to
`--file-prefix` (or `--out`) instead. Verified live against real MACE runs
on NaCl before writing this module (not assumed from `--help` alone):

- singlepoint/geomopt write ASE-readable `*.extxyz` -- energy/forces/stress
  round-trip through `ase.io.read` correctly regardless of which extra info
  columns (`spacegroup_kinds` etc.) janus-core attaches, because ASE is the
  library that wrote the file in the first place. A hand-rolled
  column-offset parser would be one janus-core version bump away from
  silently reading the wrong column. geomopt never passes `--out` (found
  live, not assumed): giving `--out` suppresses the default `*-opt.extxyz`
  entirely instead of writing both, and only the extxyz carries
  energy/forces -- the optimised-structure CIF is instead converted from
  that same extxyz's last frame, in-process, the same way janus-api's own
  helper did it (strip the extra info/array fields, re-serialize as CIF).
- eos writes `*-eos-fit.dat` (bulk_modulus/e_0/v_0) and `*-eos-raw.dat`
  (volume/energy scan points) as plain `#`-commented columns, and with
  `--plot-to-file`, a real `*-eos-plot.svg` -- same SVG janus-api's helper
  used to generate itself via matplotlib, just written by janus-core
  directly now.
- neb writes `*-neb-results.dat` (barrier/delta_E/max_force) the same way,
  and with `--write-band`/`--plot-band`, `*-neb-band.extxyz`/
  `*-neb-plot.svg`.
- phonons writes `*-thermal.yml` (phonopy's own native units -- kJ/mol/
  J-K/mol, not eV; janus-api's own helper never converted these either, it
  just returned `Phonons.results["thermal_properties"]` as-is) and, with
  `--bands --plot-to-file`, a real `*-bands.svg`. `janus`'s own CLI has no
  equivalent of janus-api's `write_yaml_band_structure()` call
  (eigenvector-bearing band YAML) -- verified against janus-core's own
  tutorial (stfc.github.io/janus-core/tutorials/cli/phonons.html), which
  visualizes bands purely from the SVG, never touching YAML either;
  `--bands` only ever produces a binary `*-auto_bands.hdf5`. `band_yaml` is
  filled back in anyway, just not from `janus` itself: `render_band_yaml.py`
  (in `./mlip-cli`, run as its own subprocess) reloads the force constants
  `janus phonons` already wrote via phonopy's own `phonopy.load()` +
  `auto_band_structure(with_eigenvectors=True, write_yaml=True)` -- the
  same auto (seekpath) path janus-core's own `*-auto_bands.hdf5` uses, just
  also exported as human/tool-readable YAML with eigenvectors instead of
  staying opaque in HDF5. Best-effort: if this step fails, `band_yaml` is
  `None`, the rest of the result is unaffected.

No more persistent warm subprocess (janus-api's `service.py` kept a loaded
MACE calculator alive between calls in the same server lifetime) -- every
call here is a fresh `janus` subprocess, so every calculation now pays
MACE's own model-load cost (confirmed multi-second), not just the first
one per server lifetime. An accepted tradeoff of dropping the HTTP
service, not an oversight.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
from pathlib import Path

import numpy as np
import yaml
from ase import Atoms
from ase.io import read as ase_read, write as ase_write

from goldilocks_agent.config import mlip_cli_path, read_mlip_enabled
from goldilocks_agent.tools.mlip_playground.models import (
    EosResult,
    GeomOptResult,
    NebResult,
    PhononsResult,
    SinglePointResult,
)

# Generous: a cold MACE-MP model load plus the calculation itself can take
# well over the httpx-era default (mirrors that module's own reasoning,
# just paid on every call now instead of only the first -- see docstring).
_TIMEOUT = 300.0


def _require_enabled() -> Path:
    if not read_mlip_enabled():
        raise RuntimeError(
            "MLIP not enabled -- set GOLDILOCKS_AGENT_MLIP_ENABLED=1 to "
            "allow MLIP Playground to run local MACE calculations (first "
            "call installs janus-core[mace] into ./mlip-cli, which "
            "downloads several GB and can take minutes)."
        )
    path = mlip_cli_path()
    if not path.is_dir():
        raise RuntimeError(f"mlip-cli directory missing: {path}")
    return path


async def _run_in_mlip_cli(executable: str, *args: str) -> None:
    """Runs `executable` inside `./mlip-cli`'s own env -- `janus` itself for
    every real calculation, or `python render_band_yaml.py` for the one
    phonopy post-processing step that fills a gap `janus` doesn't cover
    (see that script's docstring)."""
    cli_path = _require_enabled()
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "--project",
        str(cli_path),
        executable,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(
            f"{executable} {args[0]} timed out after {_TIMEOUT}s"
        ) from None
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or f"{executable} {args[0]} failed")


async def _run_janus(*args: str) -> None:
    await _run_in_mlip_cli("janus", *args)


def _read_atoms(path: Path) -> Atoms | None:
    """Last frame of a janus-written extxyz, via ASE (see module docstring
    for why not a hand-rolled parser). `None` if janus didn't write it --
    e.g. geomopt skips its default `*-opt.extxyz` entirely when `--out` is
    given instead (verified live: the two are mutually exclusive, not
    additive), which is why geomopt below never passes `--out`."""
    return ase_read(path, index=-1) if path.exists() else None


def _energy_forces_stress(
    atoms: Atoms | None,
) -> tuple[float | None, list | None, list | None]:
    if atoms is None:
        return None, None, None
    energy = forces = stress = None
    with contextlib.suppress(Exception):
        energy = float(atoms.get_potential_energy())
    with contextlib.suppress(Exception):
        forces = atoms.get_forces().tolist()
    with contextlib.suppress(Exception):
        stress = atoms.get_stress(voigt=False).tolist()
    return energy, forces, stress


def _atoms_to_cif(atoms: Atoms | None) -> str | None:
    """Same "strip calculator-attached info/arrays, write bare CIF" step
    janus-api's own `geomopt_helper.py` used to do -- ASE's CIF writer
    chokes on/bloats with extxyz's extra info fields (mace_mp_forces,
    spacegroup_kinds, ...), a clean Atoms avoids that."""
    if atoms is None:
        return None
    clean = Atoms(
        symbols=atoms.get_chemical_symbols(),
        positions=atoms.get_positions(),
        cell=atoms.get_cell(),
        pbc=atoms.get_pbc(),
    )
    buf = io.BytesIO()
    ase_write(buf, clean, format="cif")
    return buf.getvalue().decode("utf-8")


def _read_dat_columns(path: Path) -> list[list[float]]:
    """janus-core's `*-eos-{fit,raw}.dat` / `*-neb-results.dat`: one
    `#`-comment header line, then whitespace-separated float rows."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append([float(v) for v in line.split()])
    return rows


def _read_text(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


async def run_singlepoint(
    structure_content: str, structure_name: str, arch: str = "mace_mp"
) -> SinglePointResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        prefix = Path(tmp) / "result"
        await _run_janus(
            "singlepoint",
            "--arch",
            arch,
            "--struct",
            str(struct_path),
            "--file-prefix",
            str(prefix),
            "--no-tracker",
            "--no-progress-bar",
        )
        atoms = _read_atoms(Path(f"{prefix}-results.extxyz"))
        energy, forces, stress = _energy_forces_stress(atoms)
    return SinglePointResult(energy=energy, forces=forces, stress=stress)


async def run_geometry_optimization(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    fmax: float = 0.1,
    steps: int = 1000,
    relax_mode: str = "ionic",
) -> GeomOptResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        prefix = Path(tmp) / "result"
        args = [
            "geomopt",
            "--arch",
            arch,
            "--struct",
            str(struct_path),
            "--fmax",
            str(fmax),
            "--steps",
            str(steps),
            # No --out: verified live that passing it suppresses janus-core's
            # default `*-opt.extxyz` entirely (mutually exclusive, not
            # additive) -- and only that extxyz carries the final
            # energy/forces we need, CIF has no field for either. The
            # optimised structure is instead converted to CIF ourselves,
            # below, from that same extxyz's last frame.
            "--file-prefix",
            str(prefix),
            "--no-tracker",
        ]
        # Matches janus-api helper's 3 relax_mode choices: "ionic" (no cell
        # flags -- positions only, janus-core's own default with no
        # --opt-cell-* set), "cell" (hydrostatic-only volume relax), "full"
        # (janus-core's default FrechetCellFilter, full cell tensor).
        if relax_mode == "cell":
            args += [
                "--opt-cell-lengths",
                "--minimize-kwargs",
                "{'filter_kwargs': {'hydrostatic_strain': True}}",
            ]
        elif relax_mode == "full":
            args += ["--opt-cell-fully"]
        await _run_janus(*args)
        atoms = _read_atoms(Path(f"{prefix}-opt.extxyz"))
        energy, forces, _ = _energy_forces_stress(atoms)
        max_force = float(np.max(np.linalg.norm(forces, axis=1))) if forces else None
        optimised_structure = _atoms_to_cif(atoms)
    return GeomOptResult(
        final_energy=energy,
        max_force=max_force,
        optimised_structure=optimised_structure,
    )


async def run_equation_of_state(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    min_volume: float = 0.95,
    max_volume: float = 1.05,
    n_volumes: int = 7,
) -> EosResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        prefix = Path(tmp) / "result"
        await _run_janus(
            "eos",
            "--arch",
            arch,
            "--struct",
            str(struct_path),
            "--min-volume",
            str(min_volume),
            "--max-volume",
            str(max_volume),
            "--n-volumes",
            str(n_volumes),
            "--file-prefix",
            str(prefix),
            "--plot-to-file",
            "--no-tracker",
        )
        # `*-eos-fit.dat` columns, per janus-core's own header comment:
        # "#Bulk modulus [GPa] | Energy [eV] | Volume [Å^3]".
        fit_rows = _read_dat_columns(Path(f"{prefix}-eos-fit.dat"))
        bulk_modulus, e_0, v_0 = fit_rows[0] if fit_rows else (None, None, None)
        # `*-eos-raw.dat` columns: "#Lattice Scalar | Energy [eV] | Volume [Å^3]".
        raw_rows = _read_dat_columns(Path(f"{prefix}-eos-raw.dat"))
        volumes = [row[2] for row in raw_rows] or None
        energies = [row[1] for row in raw_rows] or None
        eos_svg = _read_text(Path(f"{prefix}-eos-plot.svg"))
    return EosResult(
        bulk_modulus=bulk_modulus,
        v_0=v_0,
        e_0=e_0,
        volumes=volumes,
        energies=energies,
        eos_svg=eos_svg,
    )


async def run_neb(
    init_structure_content: str,
    init_structure_name: str,
    final_structure_content: str,
    final_structure_name: str,
    arch: str = "mace_mp",
    n_images: int = 15,
    fmax: float = 0.1,
) -> NebResult:
    with tempfile.TemporaryDirectory() as tmp:
        init_path = Path(tmp) / init_structure_name
        init_path.write_text(init_structure_content)
        final_path = Path(tmp) / final_structure_name
        final_path.write_text(final_structure_content)
        prefix = Path(tmp) / "result"
        await _run_janus(
            "neb",
            "--arch",
            arch,
            "--init-struct",
            str(init_path),
            "--final-struct",
            str(final_path),
            "--n-images",
            str(n_images),
            "--fmax",
            str(fmax),
            "--minimize",
            "--write-band",
            "--plot-band",
            "--file-prefix",
            str(prefix),
            "--no-tracker",
        )
        # `*-neb-results.dat`: "#Barrier [eV] | delta E [eV] | Max force [eV/Å]".
        rows = _read_dat_columns(Path(f"{prefix}-neb-results.dat"))
        barrier, delta_e, max_force = rows[0] if rows else (None, None, None)
        neb_svg = _read_text(Path(f"{prefix}-neb-plot.svg"))
        neb_traj = _read_text(Path(f"{prefix}-neb-band.extxyz"))
    return NebResult(
        barrier=barrier,
        delta_e=delta_e,
        max_force=max_force,
        neb_svg=neb_svg,
        neb_traj=neb_traj,
    )


async def run_phonons(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    supercell: int = 2,
    displacement: float = 0.01,
) -> PhononsResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        prefix = Path(tmp) / "result"
        await _run_janus(
            "phonons",
            "--arch",
            arch,
            "--struct",
            str(struct_path),
            "--supercell",
            str(supercell),
            "--displacement",
            str(displacement),
            "--thermal",
            "--bands",
            "--plot-to-file",
            "--file-prefix",
            str(prefix),
            "--no-tracker",
            "--no-progress-bar",
        )
        temperatures = heat_capacity = entropy = free_energy = None
        thermal_path = Path(f"{prefix}-thermal.yml")
        if thermal_path.exists():
            points = yaml.safe_load(thermal_path.read_text()).get(
                "thermal_properties", []
            )
            temperatures = [p["temperature"] for p in points] or None
            heat_capacity = [p["heat_capacity"] for p in points] or None
            entropy = [p["entropy"] for p in points] or None
            free_energy = [p["free_energy"] for p in points] or None
        band_svg = _read_text(Path(f"{prefix}-bands.svg"))
        # `janus phonons` always writes force constants + a phonopy config
        # regardless of --bands/--thermal -- that's enough input for
        # phonopy's own auto_band_structure() to regenerate a real
        # eigenvector-bearing band.yaml, filling the gap `janus`'s own CLI
        # leaves (see render_band_yaml.py's docstring). Best-effort: a
        # failure here shouldn't take down the rest of a real result.
        band_yaml_path = Path(f"{prefix}-band.yaml")
        with contextlib.suppress(RuntimeError):
            await _run_in_mlip_cli(
                "python",
                str(mlip_cli_path() / "render_band_yaml.py"),
                f"{prefix}-phonopy.yml",
                f"{prefix}-force_constants.hdf5",
                str(band_yaml_path),
            )
        band_yaml = _read_text(band_yaml_path)
    return PhononsResult(
        temperatures=temperatures,
        heat_capacity=heat_capacity,
        entropy=entropy,
        free_energy=free_energy,
        band_svg=band_svg,
        band_yaml=band_yaml,
    )
