"""DFT Workbench chat tools, run on the agent server (#142).

`dft_open_structure`, `dft_settings`, `dft_set`, `dft_review`,
`dft_download_bundle` and `dft_magnetic_orderings` mirror the embedded
`goldilocks-workbench` panel's own actions on the chat's draft, which the
server keeps and the panel replays. See `tool.py`'s module docstring for
why they moved here from the browser.

This is the Tool's public surface -- callers (`tools/__init__.py`, tests)
import from here, not from `tool` directly.
"""

from __future__ import annotations

from goldilocks_agent.tools.dft_workbench.tool import (
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED,
    CONTEXT_TOOLS,
    ORIGIN_AUDITED,
    TOOL_DISPATCH,
    TOOL_SCHEMA,
    dft_download_bundle,
    dft_magnetic_orderings,
    dft_open_structure,
    dft_review,
    dft_set,
    dft_settings,
)

__all__ = [
    "CONFIRMATION_LABELS",
    "CONFIRMATION_REQUIRED",
    "CONTEXT_TOOLS",
    "ORIGIN_AUDITED",
    "TOOL_DISPATCH",
    "TOOL_SCHEMA",
    "dft_download_bundle",
    "dft_magnetic_orderings",
    "dft_open_structure",
    "dft_review",
    "dft_set",
    "dft_settings",
]
