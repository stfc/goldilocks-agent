"""LLM-facing tool wrappers for mlip_playground.

Same shape as `structure_search/tool.py`: schema lives next to the
functions it describes, and `graph.py` only ever imports the aggregated
registry from `goldilocks_agent.tools`.

Structure input is a CIF-content *string* argument, not a panel-only
structure index -- the LLM has no visibility into the frontend's
`chatStructures` picker state, so the natural chat-driven flow is
`find_in_databases` -> `get_structure` (returns CIF) -> one of these,
matching design doc 12.4's "pre-research" pipeline diagram.

These 5 tools run real local compute on the user's machine, which is why
`CONFIRMATION_REQUIRED`/`CONFIRMATION_LABELS` exist below: design doc 17.3's
interrupt mechanism gates every one of them, every single call (per the
user's explicit 2026-09-15 decision -- not "once per chat", every call),
via `graph.py`'s `call_tool`. That gate is mechanical, not prompt-based --
but the schema descriptions still tell the model to ask first in its own
words, since good framing matters even when the mechanism doesn't depend
on it. The *direct* panel "Run calculation" button in `app/src/App.jsx`
needs no such gate -- clicking it already *is* the user's explicit local
action, same reasoning as `/api/structure-match` needing none.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.mlip_playground.client import (
    run_equation_of_state,
    run_geometry_optimization,
    run_neb,
    run_phonons,
    run_singlepoint,
)
from goldilocks_agent.tools.mlip_playground.models import (
    EosResult,
    GeomOptResult,
    NebResult,
    PhononsResult,
    SinglePointResult,
)

_ARCH_SCHEMA_NOTE = (
    "Always omit this or pass 'mace_mp' -- MACE is the only potential wired "
    "up so far, other architectures janus-core supports are future work."
)

_STRUCTURE_PARAM = {
    "type": "string",
    "description": "The structure's CIF content, e.g. from a prior get_structure call.",
}
_STRUCTURE_NAME_PARAM = {
    "type": "string",
    "description": "A short filename for this structure, e.g. 'NaCl.cif'.",
}


async def run_mlip_singlepoint(
    structure_content: str, structure_name: str, arch: str = "mace_mp"
) -> SinglePointResult:
    return await run_singlepoint(structure_content, structure_name, arch)


async def run_mlip_geometry_optimization(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    fmax: float = 0.1,
    steps: int = 1000,
    relax_mode: str = "ionic",
) -> GeomOptResult:
    return await run_geometry_optimization(
        structure_content, structure_name, arch, fmax, steps, relax_mode
    )


async def run_mlip_equation_of_state(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    min_volume: float = 0.95,
    max_volume: float = 1.05,
    n_volumes: int = 7,
) -> EosResult:
    return await run_equation_of_state(
        structure_content, structure_name, arch, min_volume, max_volume, n_volumes
    )


async def run_mlip_neb(
    init_structure_content: str,
    init_structure_name: str,
    final_structure_content: str,
    final_structure_name: str,
    arch: str = "mace_mp",
    n_images: int = 15,
    fmax: float = 0.1,
) -> NebResult:
    return await run_neb(
        init_structure_content,
        init_structure_name,
        final_structure_content,
        final_structure_name,
        arch,
        n_images,
        fmax,
    )


async def run_mlip_phonons(
    structure_content: str,
    structure_name: str,
    arch: str = "mace_mp",
    supercell: int = 2,
    displacement: float = 0.01,
) -> PhononsResult:
    return await run_phonons(
        structure_content, structure_name, arch, supercell, displacement
    )


TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_mlip_singlepoint",
            "description": (
                "Run a real local MLIP (MACE) single-point energy/forces/"
                "stress calculation on a structure. This executes actual "
                "compute on the user's machine -- explain what you're about "
                "to run and why before calling this, even though the user "
                "will also be asked to confirm separately. Use this as a "
                "quick, cheap pre-check (e.g. 'is this structure "
                "reasonable/stable-looking') before recommending real DFT."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "arch": {"type": "string", "description": _ARCH_SCHEMA_NOTE},
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_mlip_geometry_optimization",
            "description": (
                "Run a real local MLIP (MACE) geometry optimization -- "
                "relaxes atomic positions (relax_mode='ionic'), the cell "
                "under hydrostatic pressure ('cell'), or both position and "
                "full cell tensor ('full'). Executes actual compute on the "
                "user's machine. Returns final_energy, max_force, and the "
                "optimised structure as CIF."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "arch": {"type": "string", "description": _ARCH_SCHEMA_NOTE},
                    "fmax": {
                        "type": "number",
                        "description": "Force convergence, eV/Å. Default 0.1.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Maximum optimisation steps. Default 1000.",
                    },
                    "relax_mode": {
                        "type": "string",
                        "enum": ["ionic", "cell", "full"],
                        "description": (
                            "'ionic': atoms only. 'cell': hydrostatic cell "
                            "pressure + atoms. 'full': full cell tensor + "
                            "atoms. Default 'ionic'."
                        ),
                    },
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_mlip_equation_of_state",
            "description": (
                "Run a real local MLIP (MACE) equation-of-state scan: "
                "energy vs. volume across a range of isotropic cell scalings, "
                "fitted to get bulk_modulus, v_0 (equilibrium volume), and "
                "e_0 (equilibrium energy). Executes actual compute on the "
                "user's machine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "arch": {"type": "string", "description": _ARCH_SCHEMA_NOTE},
                    "min_volume": {
                        "type": "number",
                        "description": "Minimum volume scaling factor. Default 0.95.",
                    },
                    "max_volume": {
                        "type": "number",
                        "description": "Maximum volume scaling factor. Default 1.05.",
                    },
                    "n_volumes": {
                        "type": "integer",
                        "description": "Number of volume points to sample. Default 7.",
                    },
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_mlip_neb",
            "description": (
                "Run a real local MLIP (MACE) nudged elastic band (NEB) "
                "calculation between two structures (e.g. before/after a "
                "diffusion hop) to estimate the migration barrier. Executes "
                "actual compute on the user's machine. Both structures must "
                "already be in CIF form (e.g. from prior get_structure or "
                "geometry-optimization calls)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "init_structure_content": {
                        "type": "string",
                        "description": "Initial-state structure's CIF content.",
                    },
                    "init_structure_name": _STRUCTURE_NAME_PARAM,
                    "final_structure_content": {
                        "type": "string",
                        "description": "Final-state structure's CIF content.",
                    },
                    "final_structure_name": _STRUCTURE_NAME_PARAM,
                    "arch": {"type": "string", "description": _ARCH_SCHEMA_NOTE},
                    "n_images": {
                        "type": "integer",
                        "description": "Number of intermediate NEB images. Default 15.",
                    },
                    "fmax": {
                        "type": "number",
                        "description": "Force convergence, eV/Å. Default 0.1.",
                    },
                },
                "required": [
                    "init_structure_content",
                    "init_structure_name",
                    "final_structure_content",
                    "final_structure_name",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_mlip_phonons",
            "description": (
                "Run a real local MLIP (MACE) phonon calculation (via "
                "Phonopy): band structure plus thermal properties (heat "
                "capacity, entropy, free energy) across a temperature "
                "range. Executes actual compute on the user's machine, and "
                "is the most expensive of these 5 calc types (supercell "
                "displacements) -- mention that before suggesting it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "arch": {"type": "string", "description": _ARCH_SCHEMA_NOTE},
                    "supercell": {
                        "type": "integer",
                        "description": "Supercell scale factor. Default 2.",
                    },
                    "displacement": {
                        "type": "number",
                        "description": "Atomic displacement in Å. Default 0.01.",
                    },
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    "run_mlip_singlepoint": run_mlip_singlepoint,
    "run_mlip_geometry_optimization": run_mlip_geometry_optimization,
    "run_mlip_equation_of_state": run_mlip_equation_of_state,
    "run_mlip_neb": run_mlip_neb,
    "run_mlip_phonons": run_mlip_phonons,
}

CONFIRMATION_REQUIRED: set[str] = set(TOOL_DISPATCH)

_CALC_DESCRIPTIONS = {
    "run_mlip_singlepoint": "a single-point energy/forces calculation",
    "run_mlip_geometry_optimization": "a geometry optimization",
    "run_mlip_equation_of_state": "an equation-of-state scan",
    "run_mlip_neb": "a nudged elastic band calculation",
    "run_mlip_phonons": "a phonon calculation",
}


def _confirmation_label(name: str, args: dict) -> str:
    struct_name = (
        args.get("structure_name") or args.get("init_structure_name") or "the structure"
    )
    return (
        f"Run {_CALC_DESCRIPTIONS.get(name, name)} on {struct_name!r} "
        "locally using MACE? This executes real compute on your machine."
    )


CONFIRMATION_LABELS: dict[str, Callable[[dict], str]] = {
    name: (lambda args, name=name: _confirmation_label(name, args))
    for name in CONFIRMATION_REQUIRED
}
