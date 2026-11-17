"""DFT Workbench chat tools -- client-executed, not server-dispatched.

`dft_open_structure`, `dft_settings`, `dft_set`, `dft_review`,
`dft_download_bundle` and `dft_magnetic_orderings` mirror the embedded
`goldilocks-workbench` panel's own actions (`source.open`, `draft.patch`,
`review.compute`, `review.refreshArchive`, `magneticOrderings.list`/`.rank`)
so chat and the panel share one state object (`app/src/App.tsx`'s
`coreWorkspace`, driven by `app/src/dftWorkbench.ts`) instead of a second,
parallel Python implementation. See `tool.py`'s module docstring for why.

This is the Tool's public surface -- callers (`tools/__init__.py`, tests)
import from here, not from `tool` directly.
"""

from __future__ import annotations

from goldilocks_agent.tools.dft_workbench.tool import (
    CLIENT_EXECUTED,
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED,
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
    "CLIENT_EXECUTED",
    "CONFIRMATION_LABELS",
    "CONFIRMATION_REQUIRED",
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
