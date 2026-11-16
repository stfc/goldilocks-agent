"""The agent server's goldilocks-core client, against recorded core answers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from goldilocks_agent.core_api import Archive, CoreApi, CoreFailure, archive_filename

FIXTURES = Path(__file__).resolve().parents[1] / "app" / "src" / "__fixtures__" / "core"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _api(handler, **kwargs) -> tuple[CoreApi, list[str]]:
    """A CoreApi whose requests go to `handler(request) -> httpx.Response`;
    also returns the "METHOD /path" of every request it sends."""
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.raw_path.decode()}")
        return handler(request)

    api = CoreApi("http://core.test", transport=httpx.MockTransport(record), **kwargs)
    return api, seen


def _refusal(status: int, kind: str, message: str, **details) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"kind": kind, "message": message, "details": details}}
    )


def test_capabilities_are_fetched_once() -> None:
    api, seen = _api(lambda request: httpx.Response(200, json=_fixture("capabilities")))
    first = asyncio.run(api.capabilities())
    second = asyncio.run(api.capabilities())
    assert first == second == _fixture("capabilities")
    assert seen == ["GET /capabilities"]


def test_a_core_on_another_vocabulary_is_refused() -> None:
    caps = {**_fixture("capabilities"), "vocabulary_version": "999"}
    api, _ = _api(lambda request: httpx.Response(200, json=caps))
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.capabilities())
    assert failure.value.kind == "vocabulary_mismatch"


def test_inspect_and_explain_return_cores_answers_and_send_the_body() -> None:
    bodies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies[request.url.path] = json.loads(request.content)
        name = {"/inspect": "inspect", "/explain": "explain"}[request.url.path]
        return httpx.Response(200, json=_fixture(name))

    api, _ = _api(handler)
    structure = _fixture("structure")
    assert asyncio.run(api.inspect(structure)) == _fixture("inspect")
    request = {**structure, "code": "quantum_espresso", "task": "scf_single_point"}
    assert asyncio.run(api.explain(request)) == _fixture("explain")
    assert bodies == {"/inspect": structure, "/explain": request}


def test_an_inspection_on_another_schema_version_is_refused() -> None:
    api, _ = _api(lambda request: httpx.Response(200, json={"schema_version": 2}))
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.inspect(_fixture("structure")))
    assert failure.value.kind == "invalid_response"


def test_cores_error_envelope_becomes_its_kind() -> None:
    api, _ = _api(
        lambda request: _refusal(
            424, "asset_not_installed", "table not installed", asset_id="sssp"
        )
    )
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.explain({}))
    assert (failure.value.kind, failure.value.status) == ("asset_not_installed", 424)
    assert failure.value.details == {"asset_id": "sssp"}


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(502, text="Bad gateway"), "http_error"),
        (
            httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
            "invalid_response",
        ),
        (
            httpx.Response(
                200, content=b"{not json", headers={"content-type": "application/json"}
            ),
            "invalid_response",
        ),
    ],
)
def test_answers_without_cores_envelope_get_a_kind_of_their_own(response, kind) -> None:
    api, _ = _api(lambda request: response)
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.explain({}))
    assert failure.value.kind == kind


def test_an_unreachable_core_is_a_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api, _ = _api(handler)
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.explain({}))
    assert failure.value.kind == "network_error"


def test_run_archive_returns_the_zip_named_as_the_workbench_names_it() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, content=b"PK\x03\x04", headers={"content-type": "application/zip"}
        )

    api, _ = _api(handler)
    request = {"structure_name": "Fe 2O3.cif", "task": "scf_single_point"}
    archive = asyncio.run(api.run_archive(request))
    assert archive == Archive(
        content=b"PK\x03\x04", filename="Fe_2O3.cif-scf_single_point.zip"
    )
    assert sent == [{**request, "respond_with": "archive"}]


def test_a_run_that_doesnt_answer_with_a_zip_is_refused() -> None:
    api, _ = _api(lambda request: httpx.Response(200, json={"files": []}))
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.run_archive({"structure_name": "x", "task": "scf"}))
    assert failure.value.kind == "invalid_response"


def test_archive_filename_falls_back_without_a_usable_name() -> None:
    assert archive_filename("", "phonon") == "goldilocks-phonon.zip"


def test_ranking_jobs_are_submitted_polled_and_cancelled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202, json={"job_id": "j/1", "state": "queued", "position": 0}
            )
        if request.method == "GET":
            return httpx.Response(
                200, json={"job_id": "j/1", "state": "running", "progress": None}
            )
        return httpx.Response(204)

    api, seen = _api(handler)
    job = asyncio.run(api.submit_ranking(_fixture("structure")))
    status = asyncio.run(api.ranking_status(job["job_id"]))
    asyncio.run(api.cancel_ranking(job["job_id"]))
    assert job["state"] == "queued" and status["state"] == "running"
    # The id is one path segment, whatever it contains.
    assert seen == [
        "POST /magnetic-orderings/rank",
        "GET /magnetic-orderings/rank/j%2F1",
        "DELETE /magnetic-orderings/rank/j%2F1",
    ]


def test_cancelling_a_job_core_no_longer_holds_is_not_a_failure() -> None:
    api, _ = _api(lambda request: _refusal(404, "unknown_ranking", "no such job"))
    asyncio.run(api.cancel_ranking("gone"))
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(api.ranking_status("gone"))
    assert failure.value.kind == "unknown_ranking"


def _states(*states: dict):
    queue = list(states)
    return lambda: queue.pop(0) if len(queue) > 1 else queue[0]


def test_ready_waits_for_a_starting_core_and_relays_its_progress() -> None:
    lines: list[str | None] = []
    api, _ = _api(
        lambda request: httpx.Response(200),
        ensure_running=_states(
            {"status": "starting", "detail": "installing assets"},
            {"status": "ready", "detail": None},
        ),
    )
    asyncio.run(api.ready(poll_s=0, on_wait=lines.append))
    assert lines == ["installing assets"]


@pytest.mark.parametrize(
    "state",
    [{"status": "ready"}, {"status": "not_configured"}],
)
def test_ready_returns_at_once_for_a_core_that_answers_or_isnt_ours(state) -> None:
    api, _ = _api(lambda request: httpx.Response(200), ensure_running=lambda: state)
    asyncio.run(api.ready(timeout_s=0))


def test_ready_fails_for_a_core_that_failed_or_is_still_starting() -> None:
    failed, _ = _api(
        lambda request: httpx.Response(200),
        ensure_running=lambda: {"status": "error", "detail": "uvx not found"},
    )
    with pytest.raises(CoreFailure, match="uvx not found") as failure:
        asyncio.run(failed.ready())
    assert failure.value.kind == "core_unavailable"

    slow, _ = _api(
        lambda request: httpx.Response(200),
        ensure_running=lambda: {"status": "starting", "detail": None},
    )
    with pytest.raises(CoreFailure) as failure:
        asyncio.run(slow.ready(timeout_s=0, poll_s=0))
    assert failure.value.kind == "core_unavailable"
