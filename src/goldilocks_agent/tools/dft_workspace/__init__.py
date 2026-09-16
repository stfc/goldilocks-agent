"""DFT Workspace: recommend Quantum ESPRESSO settings and generate real
input files from goldilocks-core v2, via its CLI (design doc twelve;
implementation plan Step 2, 2026-09-15 redone against the real v2
contract instead of v1 assumptions; redone again the same day once
goldilocks-core#62 landed, see below).

CLI, not HTTP/MCP: the CLI already covers inspect/explain/run/capabilities
identically (same underlying functions goldilocks-core's HTTP/MCP
transports call), needs no persistent service/port to manage. The one
real gap this used to have -- no CLI command returned the combined
`codes`/`tasks`/`pseudopotential_tables`/`hpc_profiles`/`warnings`-
catalog/`models`/`sources` payload HTTP/MCP's `/capabilities` did in one
call -- was filed upstream as goldilocks-core#62 and closed the same day:
`goldilocks capabilities --json` now exists, so the panel gets real
dropdown data for all of these instead of hardcoding placeholders.

This is the Tool's public surface -- callers (`server.py`, tests) import
from here, not from `client`/`models`/`tool` directly. LLM-facing tool
schema (`dft_explain`/`dft_generate`, `tool.py`) landed 2026-09-16 -- the
panel-first, tool-node-second phasing Find in Databases/MLIP Playground
both used, now caught up for DFT Workspace too.
"""

from __future__ import annotations

from goldilocks_agent.tools.dft_workspace.client import (
    capabilities,
    explain,
    inspect_structure,
    run,
    run_bundle,
)
from goldilocks_agent.tools.dft_workspace.models import (
    CapabilitiesResult,
    CodeInfo,
    ExplainResult,
    FactInfo,
    HpcProfileInfo,
    InspectResult,
    PseudoTableInfo,
    ResolvedField,
    RunResult,
    SettingSpec,
    TaskInfo,
    WarningCatalogEntry,
)
from goldilocks_agent.tools.dft_workspace.tool import (
    TOOL_DISPATCH,
    TOOL_SCHEMA,
    dft_explain,
    dft_generate,
)

__all__ = [
    "TOOL_DISPATCH",
    "TOOL_SCHEMA",
    "CapabilitiesResult",
    "CodeInfo",
    "ExplainResult",
    "FactInfo",
    "HpcProfileInfo",
    "InspectResult",
    "PseudoTableInfo",
    "ResolvedField",
    "RunResult",
    "SettingSpec",
    "TaskInfo",
    "WarningCatalogEntry",
    "capabilities",
    "dft_explain",
    "dft_generate",
    "explain",
    "inspect_structure",
    "run",
    "run_bundle",
]
