"""LLM-facing tool wrappers for dft_workbench.

Same shape as `structure_search/tool.py`: schema lives next to the
functions it describes, `graph.py` only ever imports the aggregated
registry from `goldilocks_agent.tools`. No `CONFIRMATION_REQUIRED`/
`CONFIRMATION_LABELS` exports here (unlike `mlip_playground/tool.py`) --
design doc 17.3's interrupt gate is for expensive/dangerous local compute
(MLIP's real MACE calculations); generating a QE input deck is cheap,
local file writes with no side effect worth confirming, same reasoning
`server.py`'s `/api/dft/explain`/`/api/dft/run` routes already use for the
panel's own "Explain"/"Generate" buttons needing no gate either.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from goldilocks_agent.tools.dft_workbench.client import explain, run
from goldilocks_agent.tools.dft_workbench.models import ExplainResult, RunResult

_TASK_NOTE = (
    "'quantum_espresso' is the only real code, and 'scf_single_point'/"
    "'dos'/'relax'/'vc-relax' the only real tasks goldilocks-core "
    "generates inputs for today -- omit code/task rather than inventing "
    "other values; goldilocks-core defaults task to 'scf_single_point'."
)
_STRUCTURE_PARAM = {
    "type": "string",
    "description": "The structure's CIF content, e.g. from a prior get_structure call.",
}
_STRUCTURE_NAME_PARAM = {
    "type": "string",
    "description": "A short filename for this structure, e.g. 'NaCl.cif'.",
}
_HPC_PARAM = {
    "type": "string",
    "description": (
        "HPC profile name (e.g. 'scarf'). Omit if only one profile is "
        "installed -- goldilocks-core resolves it automatically then."
    ),
}
_OVERRIDES_PARAM = {
    "type": "object",
    "description": (
        "Optional sparse overrides, e.g. {'ecutwfc_ry': 60}. "
        "goldilocks-core's advisors auto-resolve every DFT setting from "
        "the structure itself -- only pass a key here if the user "
        "explicitly wants it pinned to a specific value, don't fill in "
        "settings yourself just because you could."
    ),
}


async def dft_explain(
    structure_content: str,
    structure_name: str,
    code: str | None = None,
    task: str | None = None,
    hpc: str | None = None,
    overrides: dict[str, object] | None = None,
) -> ExplainResult:
    return await explain(structure_content, structure_name, code, task, hpc, overrides)


async def dft_generate(
    structure_content: str,
    structure_name: str,
    code: str | None = None,
    task: str | None = None,
    hpc: str | None = None,
    overrides: dict[str, object] | None = None,
) -> RunResult:
    return await run(structure_content, structure_name, code, task, hpc, overrides)


TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "dft_explain",
            "description": (
                "Run goldilocks-core's real DFT analysis and advisors on a "
                "structure to see which settings (exchange-correlation "
                "functional, plane-wave cutoffs, k-point sampling, "
                "pseudopotential table, magnetism, Hubbard U, ...) it would "
                "recommend and *why*, without generating any files. Use this "
                "to answer questions like 'what functional/cutoff would you "
                "use for this' or to sanity-check a structure before "
                "generating real inputs -- not for running an actual "
                "calculation (goldilocks-core only recommends DFT settings, "
                "it doesn't run Quantum ESPRESSO itself). Each returned "
                "record's `source` says who resolved it (human/ml/llm/"
                "heuristic); `status: unavailable`/`blocked` records "
                "explain why that one couldn't be resolved. " + _TASK_NOTE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "code": {
                        "type": "string",
                        "description": "DFT code id. " + _TASK_NOTE,
                    },
                    "task": {
                        "type": "string",
                        "enum": ["scf_single_point", "dos", "relax", "vc-relax"],
                        "description": "Calculation task. " + _TASK_NOTE,
                    },
                    "hpc": _HPC_PARAM,
                    "overrides": _OVERRIDES_PARAM,
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dft_generate",
            "description": (
                "Generate a real, runnable Quantum ESPRESSO input deck for a "
                "structure via goldilocks-core -- the actual input file and "
                "a SLURM submission script, ready to run on the target HPC "
                "profile. Use this once the user wants real files, not just "
                "an explanation of what settings would be used (call "
                "dft_explain for that). The pseudopotential file itself is "
                "not included in what you get back (it's plain text but "
                "large) -- it's part of the real generated bundle, tell the "
                "user it's there and downloadable from the DFT Workbench "
                "panel's Inputs tab (a one-click 'Download bundle' button) "
                "if they need the actual file, not just the input/script "
                "text. " + _TASK_NOTE
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "structure_content": _STRUCTURE_PARAM,
                    "structure_name": _STRUCTURE_NAME_PARAM,
                    "code": {
                        "type": "string",
                        "description": "DFT code id. " + _TASK_NOTE,
                    },
                    "task": {
                        "type": "string",
                        "enum": ["scf_single_point", "dos", "relax", "vc-relax"],
                        "description": "Calculation task. " + _TASK_NOTE,
                    },
                    "hpc": _HPC_PARAM,
                    "overrides": _OVERRIDES_PARAM,
                },
                "required": ["structure_content", "structure_name"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Callable[..., Awaitable]] = {
    "dft_explain": dft_explain,
    "dft_generate": dft_generate,
}
