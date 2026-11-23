"""DFT Workbench chat tools, run on the agent server (#142).

The first half pins the contract `graph.py`'s `call_tool` relies on. The
second runs the tools themselves on a chat's draft, against goldilocks-core's
recorded answers (`app/src/__fixtures__/core`) and a real database -- with
no browser, which is what moving them to the server was for.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import HumanMessage

from goldilocks_agent import store
from goldilocks_agent.core_api import CoreApi
from goldilocks_agent.tools.context import ToolContext
from goldilocks_agent.tools.dft_workbench import (
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED,
    CONTEXT_TOOLS,
    ORIGIN_AUDITED,
    TOOL_DISPATCH,
    TOOL_SCHEMA,
)
from goldilocks_agent.tools.dft_workbench.bundles import bundle_path

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "src" / "__fixtures__" / "core"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# --- The contract ------------------------------------------------------------


def _parameters(name: str) -> dict:
    by_name = {entry["function"]["name"]: entry for entry in TOOL_SCHEMA}
    return by_name[name]["function"]["parameters"]


def test_every_tool_works_on_the_chat_itself() -> None:
    schema_names = {entry["function"]["name"] for entry in TOOL_SCHEMA}
    assert schema_names == set(TOOL_DISPATCH) == CONTEXT_TOOLS


def test_nothing_needs_a_confirmation_card() -> None:
    # Unlike MLIP Playground's real local compute, none of these are
    # consequential *to the user's machine* on their own -- the browser
    # already forces a genuine click on the Download button before anything
    # lands on disk (tool.py's CONFIRMATION_REQUIRED comment, 2026-09-25).
    assert not CONFIRMATION_REQUIRED
    assert not CONFIRMATION_LABELS


def test_review_and_download_bundle_take_no_parameters() -> None:
    # They act on the chat's draft, which dft_set and the panel change.
    assert _parameters("dft_review")["properties"] == {}
    assert _parameters("dft_download_bundle")["properties"] == {}


def test_open_structure_takes_a_file_name_not_file_content() -> None:
    # #73: the model names a structure file in the chat and the server reads
    # it -- re-typing the file into the call was slow and smaller models
    # silently truncated or altered it.
    parameters = _parameters("dft_open_structure")
    assert set(parameters["properties"]) == {"file_name"}
    assert parameters["required"] == ["file_name"]
    assert parameters["additionalProperties"] is False


def test_set_takes_any_setting_keyed_by_cores_own_key() -> None:
    # One map for every panel control, keyed by core's override keys, not an
    # enum: the server checks them against core's capabilities (design 3.3).
    parameters = _parameters("dft_set")
    assert parameters["required"] == ["values"]
    assert parameters["properties"]["values"]["type"] == "object"
    assert "properties" not in parameters["properties"]["values"]
    assert parameters["additionalProperties"] is False


def test_set_is_the_only_tool_that_records_the_user_message() -> None:
    # design 3.3: relay traces back to what the user said. The runtime
    # injects the id; no tool takes it as a parameter.
    assert ORIGIN_AUDITED == {"dft_set"} <= CONTEXT_TOOLS
    for entry in TOOL_SCHEMA:
        assert "origin" not in str(entry["function"]["parameters"])


def test_settings_take_only_optional_narrowing() -> None:
    assert set(_parameters("dft_settings")["properties"]) == {"group"}
    assert "required" not in _parameters("dft_settings")


def test_orderings_only_list_never_rank() -> None:
    # Ranking stays the panel's Run mMACE button (Junwen, 2026-11-16).
    assert _parameters("dft_magnetic_orderings")["properties"] == {}
    assert "required" not in _parameters("dft_magnetic_orderings")


# --- The tools on a chat's draft --------------------------------------------

STRUCTURE = _fixture("structure")
ATTACHED = HumanMessage(
    content=f"Set this up.\n\n[Attached file: {STRUCTURE['structure_name']}]\n"
    f"```cif\n{STRUCTURE['structure_content']}```"
)


class FakeCore:
    """goldilocks-core answering from its recorded responses; `answers`
    overrides a route ("POST /explain") with a function of the request."""

    def __init__(self, **answers: Callable[[dict], httpx.Response]) -> None:
        self.requests: list[tuple[str, dict | None]] = []
        self.answers = answers

    def __call__(self, request: httpx.Request) -> httpx.Response:
        route = f"{request.method} {request.url.path}"
        body = json.loads(request.content) if request.content else None
        self.requests.append((route, body))
        if route in self.answers:
            return self.answers[route](body)
        if route == "GET /capabilities":
            return httpx.Response(200, json=_fixture("capabilities"))
        if route == "POST /inspect":
            return httpx.Response(200, json=_fixture("inspect"))
        if route == "POST /explain":
            return httpx.Response(200, json=_fixture("explain"))
        if route == "POST /run":
            return httpx.Response(
                200, content=b"PK\x03\x04", headers={"content-type": "application/zip"}
            )
        return httpx.Response(
            404, json={"error": {"kind": "not_found", "message": route}}
        )

    def bodies(self, route: str) -> list[dict | None]:
        return [body for r, body in self.requests if r == route]


def _chat(tmp_path, monkeypatch, core: FakeCore, *, messages=(ATTACHED,)):
    """Runs `scenario(call, events)` in a chat: `call(tool, **args)` runs one
    tool with a fresh ToolContext; `events` collects what it sent."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "goldilocks.db")

    def run(scenario):
        async def main():
            events: list[dict] = []
            api = CoreApi(
                "http://core.test",
                transport=httpx.MockTransport(core),
                ensure_running=lambda: {"status": "ready"},
            )
            async with store.open_store() as db:

                async def call(tool: str, **args):
                    context = ToolContext("t1", list(messages), events.append, db, api)
                    return await TOOL_DISPATCH[tool](context, **args)

                result = await scenario(call, events, db)
            await api.aclose()
            return result

        return asyncio.run(main())

    return run


def _drafts(events: list[dict]) -> list[dict]:
    return [event["draft"] for event in events if event["type"] == "dft_draft"]


def test_open_structure_opens_the_chats_file_and_saves_the_draft(
    tmp_path, monkeypatch
) -> None:
    core = FakeCore()

    async def scenario(call, events, db):
        result = await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        return result, await store.get_dft_draft(db, "t1")

    result, saved = _chat(tmp_path, monkeypatch, core)(scenario)
    # Core inspected the chat's own file text, not anything the model typed.
    assert core.bodies("POST /inspect") == [{**STRUCTURE, "structure_format": "cif"}]
    assert result["structure"]["formula"] == _fixture("inspect")["structure"]["formula"]
    assert result["calculation"]["task"] == "scf_single_point"
    assert result["tasks_available"][0] == "scf_single_point"
    assert saved["revision"] == 1
    assert saved["structure"]["structure_name"] == STRUCTURE["structure_name"]


def test_open_structure_names_the_files_there_are(tmp_path, monkeypatch) -> None:
    async def scenario(call, events, db):
        with pytest.raises(ValueError, match="Structure files in this chat") as refused:
            await call("dft_open_structure", file_name="nope.cif")
        return str(refused.value), events

    message, events = _chat(tmp_path, monkeypatch, FakeCore())(scenario)
    assert STRUCTURE["structure_name"] in message
    assert _drafts(events) == []


def test_set_checks_with_core_then_commits_and_tells_the_browser(
    tmp_path, monkeypatch
) -> None:
    core = FakeCore()

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        result = await call("dft_set", values={"task": "phonon", "q_grid": [4, 4, 4]})
        return result, events, await store.get_dft_draft(db, "t1")

    result, events, saved = _chat(tmp_path, monkeypatch, core)(scenario)
    assert result["changed"] == {"task": "phonon", "q_grid": [4, 4, 4]}
    assert "records" in result
    last_explain = core.bodies("POST /explain")[-1]
    assert (last_explain["task"], last_explain["overrides"]) == (
        "phonon",
        {"q_grid": [4, 4, 4]},
    )
    assert (saved["revision"], saved["task"]) == (2, "phonon")
    assert _drafts(events)[-1] == saved


def test_set_commits_nothing_core_refuses(tmp_path, monkeypatch) -> None:
    refusal = {"error": {"kind": "invalid_request", "message": "no step reads q_grid"}}
    core = FakeCore()

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        core.answers["POST /explain"] = lambda body: httpx.Response(422, json=refusal)
        with pytest.raises(
            ValueError, match="Nothing was set, goldilocks-core refused it"
        ):
            await call("dft_set", values={"walltime_h": 24})
        return await store.get_dft_draft(db, "t1"), events

    saved, events = _chat(tmp_path, monkeypatch, core)(scenario)
    assert saved["revision"] == 1
    assert "walltime_h" not in saved["overrides"]
    assert len(_drafts(events)) == 1


def test_set_keeps_a_change_core_couldnt_review(tmp_path, monkeypatch) -> None:
    """Core failing on its side (500) says nothing about the draft: the
    change stays, as it did in the browser."""
    core = FakeCore()

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        core.answers["POST /explain"] = lambda body: httpx.Response(
            500, json={"error": {"kind": "internal_error", "message": "boom"}}
        )
        with pytest.raises(
            ValueError,
            match="The settings were changed, but goldilocks-core couldn't review them",
        ):
            await call("dft_set", values={"walltime_h": 24})
        return await store.get_dft_draft(db, "t1")

    saved = _chat(tmp_path, monkeypatch, core)(scenario)
    assert saved["overrides"]["walltime_h"] == 24


def test_set_before_a_structure_is_open_needs_no_core_review(
    tmp_path, monkeypatch
) -> None:
    core = FakeCore()

    async def scenario(call, events, db):
        result = await call("dft_set", values={"task": "band", "walltime_h": 12})
        opened = await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        return result, opened

    result, opened = _chat(tmp_path, monkeypatch, core)(scenario)
    assert result == {
        "changed": {"task": "band", "walltime_h": 12},
        "calculation": {
            "code": "quantum_espresso",
            "task": "band",
            "hpc": None,
            "overrides": {"walltime_h": 12},
        },
    }
    # The structure opens into the task and keeps the method setting.
    assert opened["calculation"]["task"] == "band"
    assert opened["calculation"]["overrides"] == {"walltime_h": 12}
    assert core.bodies("POST /explain") == []


def test_set_with_nothing_new_commits_nothing(tmp_path, monkeypatch) -> None:
    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        await call("dft_set", values={"task": "scf_single_point"})
        return await store.get_dft_draft(db, "t1"), events

    saved, events = _chat(tmp_path, monkeypatch, FakeCore())(scenario)
    assert saved["revision"] == 1
    assert len(_drafts(events)) == 1


def test_review_needs_an_open_structure(tmp_path, monkeypatch) -> None:
    async def scenario(call, events, db):
        with pytest.raises(ValueError, match="call dft_open_structure first"):
            await call("dft_review")
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        return await call("dft_review")

    review = _chat(tmp_path, monkeypatch, FakeCore())(scenario)
    assert set(review) == {"calculation", "records", "warnings", "blocking"}


def test_download_bundle_refuses_while_blocked_then_keeps_the_bundle(
    tmp_path, monkeypatch
) -> None:
    core = FakeCore()

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        core.answers["POST /explain"] = lambda body: httpx.Response(
            200, json=_fixture("explain-blocked")
        )
        with pytest.raises(ValueError, match="Input files can't be generated"):
            await call("dft_download_bundle")
        core.answers.pop("POST /explain")
        return await call("dft_download_bundle")

    result = _chat(tmp_path, monkeypatch, core)(scenario)
    stem = Path(STRUCTURE["structure_name"]).name
    assert result["filename"] == f"{stem}-scf_single_point.zip"
    assert result["download_url"] == f"/api/chat/t1/dft/bundles/{result['filename']}"
    assert bundle_path("t1", result["filename"]).read_bytes() == b"PK\x03\x04"
    assert core.bodies("POST /run")[-1]["respond_with"] == "archive"


def test_magnetic_orderings_need_a_spin_polarized_calculation(
    tmp_path, monkeypatch
) -> None:
    explain = _fixture("explain")
    magnetic = explain["records"]["magnetic"]
    polarized = {
        **explain,
        "records": {
            **explain["records"],
            "magnetic": {
                **magnetic,
                "value": {**magnetic["value"], "spin_polarized": True},
            },
        },
    }
    listing = {
        "ranked": False,
        "candidates": [
            {
                "label": "fm",
                "formula": "Si8",
                "natoms": 8,
                "energy_per_atom_ev": None,
                "status": None,
                "is_recommended": False,
                "overrides": {"magnetic_ordering": "fm"},
            },
        ],
        "warnings": [],
    }
    core = FakeCore(
        **{"POST /magnetic-orderings": lambda body: httpx.Response(200, json=listing)}
    )

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        with pytest.raises(ValueError, match="isn't spin-polarized"):
            await call("dft_magnetic_orderings")
        core.answers["POST /explain"] = lambda body: httpx.Response(200, json=polarized)
        return await call("dft_magnetic_orderings")

    result = _chat(tmp_path, monkeypatch, core)(scenario)
    assert result["candidates"][0]["label"] == "fm"
    # The candidates' override values stay with core and the panel.
    assert "magnetic_ordering" not in json.dumps(result)


def test_a_structure_core_refuses_leaves_the_draft_as_it_was(
    tmp_path, monkeypatch
) -> None:
    """The structure is inspected before anything is committed: one core
    can't read is the tool's error, and the open one stays open."""
    broken = HumanMessage(
        content="[Attached file: broken.cif]\n```cif\ndata_broken\n```"
    )
    core = FakeCore()

    async def scenario(call, events, db):
        await call("dft_open_structure", file_name=STRUCTURE["structure_name"])
        core.answers["POST /inspect"] = lambda body: httpx.Response(
            422, json={"error": {"kind": "invalid_structure", "message": "no atoms"}}
        )
        with pytest.raises(ValueError, match="invalid_structure: no atoms"):
            await call("dft_open_structure", file_name="broken.cif")
        return await store.get_dft_draft(db, "t1"), events

    saved, events = _chat(tmp_path, monkeypatch, core, messages=(ATTACHED, broken))(
        scenario
    )
    assert saved["structure"]["structure_name"] == STRUCTURE["structure_name"]
    assert len(_drafts(events)) == 1
