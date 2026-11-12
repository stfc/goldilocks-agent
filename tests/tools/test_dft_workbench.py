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


def test_client_executed_covers_every_tool() -> None:
    assert set(TOOL_DISPATCH) == CLIENT_EXECUTED


def test_nothing_needs_a_confirmation_card() -> None:
    # Unlike MLIP Playground's real local compute, none of these are
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
    # They act on the embedded Workbench's own `draft` state, which dft_set
    # and the panel change -- see tool.py's module docstring.
    by_name = {entry["function"]["name"]: entry for entry in TOOL_SCHEMA}
    assert by_name["dft_review"]["function"]["parameters"]["properties"] == {}
    assert by_name["dft_download_bundle"]["function"]["parameters"]["properties"] == {}


def test_open_structure_takes_a_file_name_not_file_content() -> None:
    # #73: the model names a structure file in the chat and the browser
    # reads it -- re-typing the file into the call was slow and smaller
    # models silently truncated or altered it.
    by_name = {entry["function"]["name"]: entry for entry in TOOL_SCHEMA}
    parameters = by_name["dft_open_structure"]["function"]["parameters"]
    assert set(parameters["properties"]) == {"file_name"}
    assert parameters["required"] == ["file_name"]
    assert parameters["additionalProperties"] is False


def _parameters(name: str) -> dict:
    by_name = {entry["function"]["name"]: entry for entry in TOOL_SCHEMA}
    return by_name[name]["function"]["parameters"]


def test_set_takes_any_setting_keyed_by_cores_own_key() -> None:
    # One map for every panel control -- task, code, HPC profile and every
    # override -- keyed by core's override keys, not an enum: the browser
    # checks them against core's capabilities (design 3.3).
    parameters = _parameters("dft_set")
    assert parameters["required"] == ["values"]
    assert parameters["properties"]["values"]["type"] == "object"
    assert "properties" not in parameters["properties"]["values"]
    assert parameters["additionalProperties"] is False


def test_set_is_the_only_tool_that_records_the_user_message() -> None:
    # design 3.3: relay traces back to what the user said. The runtime
    # injects the id; no tool takes it as a parameter.
    assert ORIGIN_AUDITED == {"dft_set"} <= CLIENT_EXECUTED
    for entry in TOOL_SCHEMA:
        assert "origin" not in str(entry["function"]["parameters"])


def test_settings_and_orderings_take_only_optional_narrowing() -> None:
    assert set(_parameters("dft_settings")["properties"]) == {"group"}
    assert "required" not in _parameters("dft_settings")
    assert set(_parameters("dft_magnetic_orderings")["properties"]) == {"rank"}
    assert "required" not in _parameters("dft_magnetic_orderings")


@pytest.mark.parametrize(
    "stub",
    [
        dft_open_structure,
        dft_settings,
        dft_set,
        dft_review,
        dft_download_bundle,
        dft_magnetic_orderings,
    ],
)
def test_stub_raises_if_ever_actually_dispatched(stub) -> None:
    # Defensive contract: graph.py's CLIENT_EXECUTED_TOOLS check must
    # always intercept these before TOOL_DISPATCH.get(name) would be
    # called -- if that check is ever bypassed, this should fail loudly,
    # not silently no-op or return a fake result.
    with pytest.raises(RuntimeError, match="client-executed"):
        asyncio.run(stub())
