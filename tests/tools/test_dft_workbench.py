"""DFT Workbench chat tools (2026-09-25) -- client-executed, not
server-dispatched (see `tool.py`'s module docstring). Nothing here runs
real compute or talks to goldilocks-core -- that only happens in the
browser, via `app/src/App.tsx`'s `dispatchDftTool`. These tests just pin
the aggregation contract `graph.py`'s `call_tool` relies on, and the
defensive "must never actually be dispatched server-side" behaviour of
each stub.
"""

from __future__ import annotations

import asyncio

import pytest

from goldilocks_agent.tools.dft_workbench import (
    CLIENT_EXECUTED,
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED,
    TOOL_DISPATCH,
    TOOL_SCHEMA,
    dft_download_bundle,
    dft_open_structure,
    dft_review,
)


def test_client_executed_covers_every_tool() -> None:
    assert set(TOOL_DISPATCH) == CLIENT_EXECUTED


def test_nothing_needs_a_confirmation_card() -> None:
    # Unlike MLIP Playground's real local compute, none of these three are
    # consequential *to the user's machine* on their own -- the browser
    # already forces a genuine click on the Bundle card's own Download
    # button before anything lands on disk, so a chat-level confirmation
    # card ahead of dft_download_bundle would just gate nothing real (see
    # tool.py's CONFIRMATION_REQUIRED comment, removed 2026-09-25).
    assert not CONFIRMATION_REQUIRED
    assert not CONFIRMATION_LABELS


def test_schema_names_match_dispatch_and_client_executed() -> None:
    schema_names = {entry["function"]["name"] for entry in TOOL_SCHEMA}
    assert schema_names == set(TOOL_DISPATCH) == CLIENT_EXECUTED


def test_review_and_download_bundle_take_no_parameters() -> None:
    # Deliberate v1 scope: code/task/hpc/overrides live in the embedded
    # Workbench's own `draft` state, not exposed here -- see tool.py's
    # module docstring for why (no fixed enum to validate against).
    by_name = {entry["function"]["name"]: entry for entry in TOOL_SCHEMA}
    assert by_name["dft_review"]["function"]["parameters"]["properties"] == {}
    assert by_name["dft_download_bundle"]["function"]["parameters"]["properties"] == {}


@pytest.mark.parametrize(
    "stub",
    [dft_open_structure, dft_review, dft_download_bundle],
)
def test_stub_raises_if_ever_actually_dispatched(stub) -> None:
    # Defensive contract: graph.py's CLIENT_EXECUTED_TOOLS check must
    # always intercept these before TOOL_DISPATCH.get(name) would be
    # called -- if that check is ever bypassed, this should fail loudly,
    # not silently no-op or return a fake result.
    with pytest.raises(RuntimeError, match="client-executed"):
        asyncio.run(stub())
