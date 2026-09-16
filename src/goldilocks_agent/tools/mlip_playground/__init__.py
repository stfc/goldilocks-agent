"""MLIP Playground: run real local MLIP (MACE, via janus-core's own `janus`
CLI) calculations -- singlepoint, geometry optimization, equation of state,
NEB, phonons (design doc 12.4's "pre-research" pipeline, alongside Find in
Databases; implementation plan Step 7).

This is the Tool's public surface -- callers (`server.py`, tests) import
from here, not from `client`/`service`/`models` directly.
"""

from __future__ import annotations

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
from goldilocks_agent.tools.mlip_playground.tool import (
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED,
    TOOL_DISPATCH,
    TOOL_SCHEMA,
)

__all__ = [
    "CONFIRMATION_LABELS",
    "CONFIRMATION_REQUIRED",
    "TOOL_DISPATCH",
    "TOOL_SCHEMA",
    "EosResult",
    "GeomOptResult",
    "NebResult",
    "PhononsResult",
    "SinglePointResult",
    "run_equation_of_state",
    "run_geometry_optimization",
    "run_neb",
    "run_phonons",
    "run_singlepoint",
]
