"""Result models for the 5 janus-api calc types this Tool wraps (singlepoint,
geomopt, eos, neb, phonons -- the ones the reference `janus-api` prototype
and the already-built frontend panel both agree on; elastic constants/MD are
real janus-core capabilities but aren't in either of those, so they're not
here either -- future work, not silently added or silently dropped).

Field names match janus-api's own helper return dicts field-for-field
(verified 2026-09-15 against `janus_api/utils/*_helper.py`, back when this
Tool still shelled out to that vendored FastAPI wrapper) -- and therefore
also match what the already-built `app/src/App.jsx` MLIP panel already
reads off `result.raw.*`. `client.py` was later rewritten (2026-09-15, same
day) to shell out to janus-core's own `janus` CLI directly instead --
janus-api is gone, but these field names were kept as the stable contract
with the frontend, not reset to whatever the CLI's own file layout happens
to call things. One field, `PhononsResult.band_yaml`, lost its original source when
janus-api's Python-level `write_yaml_band_structure()` call went away with
it -- `client.py` regenerates it a different way now (a small phonopy
post-processing subprocess, `./mlip-cli/render_band_yaml.py`, best-effort:
`None` if that step fails), see `client.py`'s docstring.

Every model also carries a `_LLM_EXCLUDE` set of its heavy visual/array
fields (SVG plot strings, extxyz trajectories, full CIF blobs) and a
`model_dump_for_llm()` that drops them. The direct REST path
(`/api/mlip/*` in server.py) is panel-only and always wants the full
`model_dump()` -- but the LLM tool-calling path's output gets
`json.dumps()`'d into a `tool` message that persists in the checkpointer's
history forever, so shipping raw SVG XML into every subsequent call on that
thread would be real, growing waste. `graph.py`'s `call_tool` prefers
`model_dump_for_llm()` when present -- a small generic hook any future
tool with a heavy visual payload can reuse, not MLIP-specific.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel


class _LLMTrimmedModel(BaseModel):
    _LLM_EXCLUDE: ClassVar[set[str]] = set()

    def model_dump_for_llm(self) -> dict:
        return self.model_dump(exclude=self._LLM_EXCLUDE)


class SinglePointResult(_LLMTrimmedModel):
    energy: float | None = None
    forces: list | None = None
    stress: list | None = None

    _LLM_EXCLUDE: ClassVar[set[str]] = {"forces"}

    def summary(self) -> str:
        if self.energy is None:
            return "Single-point calculation completed (no energy reported)."
        return f"Single-point energy: {self.energy:.4f} eV."


class GeomOptResult(_LLMTrimmedModel):
    final_energy: float | None = None
    max_force: float | None = None
    optimised_structure: str | None = None

    _LLM_EXCLUDE: ClassVar[set[str]] = {"optimised_structure"}

    def summary(self) -> str:
        if self.final_energy is None:
            return "Geometry optimization completed (no final energy reported)."
        force_part = (
            f", max force {self.max_force:.4f} eV/Å"
            if self.max_force is not None
            else ""
        )
        return (
            f"Geometry optimization: final energy {self.final_energy:.4f} eV"
            f"{force_part}."
        )


class EosResult(_LLMTrimmedModel):
    bulk_modulus: float | None = None
    v_0: float | None = None
    e_0: float | None = None
    volumes: list[float] | None = None
    energies: list[float] | None = None
    eos_svg: str | None = None

    _LLM_EXCLUDE: ClassVar[set[str]] = {"volumes", "energies", "eos_svg"}

    def summary(self) -> str:
        if self.bulk_modulus is None:
            return "Equation-of-state scan completed (no bulk modulus reported)."
        return (
            f"Equation of state: bulk modulus {self.bulk_modulus:.1f} GPa, "
            f"V₀={self.v_0:.3f} Å³, E₀={self.e_0:.4f} eV."
        )


class NebResult(_LLMTrimmedModel):
    barrier: float | None = None
    delta_e: float | None = None
    max_force: float | None = None
    neb_svg: str | None = None
    neb_traj: str | None = None

    _LLM_EXCLUDE: ClassVar[set[str]] = {"neb_svg", "neb_traj"}

    def summary(self) -> str:
        if self.barrier is None:
            return "NEB calculation completed (no barrier reported)."
        return f"NEB barrier: {self.barrier:.4f} eV (ΔE={self.delta_e:.4f} eV)."


class PhononsResult(_LLMTrimmedModel):
    temperatures: list[float] | None = None
    heat_capacity: list[float] | None = None
    entropy: list[float] | None = None
    free_energy: list[float] | None = None
    band_svg: str | None = None
    band_yaml: str | None = None

    _LLM_EXCLUDE: ClassVar[set[str]] = {"band_svg", "band_yaml"}

    def summary(self) -> str:
        if not self.temperatures:
            return "Phonon calculation completed (no thermal properties reported)."
        n = len(self.temperatures)
        return f"Phonon calculation completed across {n} temperature points."
