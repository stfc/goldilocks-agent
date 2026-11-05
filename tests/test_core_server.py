"""`core_server.py`'s spawn-command construction and bring-up state. Pure
Python, no subprocess/network -- asserting on the *constructed command* and
reported state, not real execution, same "which tool/args, not what happens"
philosophy the rest of this test suite already follows for
confirmation-gated tools.
"""

from __future__ import annotations

import asyncio
import io
import shlex
import time

import httpx
import pytest

from goldilocks_agent import core_server


def test_checkout_mode_always_requests_the_http_extra() -> None:
    # Without this, `uv run`'s own implicit sync only resolves the default
    # dependency set, not optional extras -- a fresh checkout fails with
    # "The HTTP transport requires goldilocks-core[http]" (reproduced live).
    assert core_server._spawn_command("/opt/goldilocks-core") == [
        "uv",
        "run",
        "--directory",
        "/opt/goldilocks-core",
        "--extra",
        "http",
        "poe",
        "serve",
    ]


def test_pypi_mode_installs_assets_then_serves() -> None:
    uvx = f"uvx --from {shlex.quote(core_server.CORE_PYPI_REQUIREMENT)} goldilocks"
    # Assets first, the way checkout mode's `poe serve` does: the Workbench
    # sends fetch_missing=false, so a machine without them gets HTTP 424.
    assert core_server._spawn_command(None) == [
        "sh",
        "-c",
        f"{uvx} assets install workbench && "
        f"exec {uvx} serve http --host 127.0.0.1 --port 8000",
    ]


def test_spawn_leaves_the_mace_backbone_to_core(monkeypatch) -> None:
    # Core installs mMACE's backbone with its own assets; a
    # GOLDILOCKS_MACE_BACKBONE set here would override that copy.
    monkeypatch.delenv("GOLDILOCKS_MACE_BACKBONE", raising=False)
    captured = {}

    def fake_popen(command, **kwargs):
        captured["env"] = kwargs["env"]

        class _FakeProcess:
            stdout = io.StringIO("")

        return _FakeProcess()

    monkeypatch.setattr(core_server.subprocess, "Popen", fake_popen)

    core_server._spawn("/opt/goldilocks-core")

    assert "GOLDILOCKS_MACE_BACKBONE" not in captured["env"]


def test_spawned_core_output_is_forwarded_with_a_prefix(capsys) -> None:
    # Core's tracebacks (e.g. a fail-closed ML error behind an HTTP 500) must
    # land somewhere readable -- this process's stderr, i.e. `docker logs`.
    core_server._recent_output.clear()
    core_server._forward_output(io.StringIO("starting\nValueError: bad model\n\n"))

    assert capsys.readouterr().err == (
        "[goldilocks-core] starting\n"
        "[goldilocks-core] ValueError: bad model\n"
        "[goldilocks-core] \n"
    )
    assert core_server._with_last_output("core exited.") == (
        "core exited. Last output: ValueError: bad model"
    )


def _mark_ready_against(capabilities: httpx.Response, **kwargs) -> dict:
    transport = httpx.MockTransport(lambda request: capabilities)

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url=core_server.CORE_SERVER_BASE_URL, transport=transport
        ) as client:
            await core_server._mark_ready(client, **kwargs)

    asyncio.run(run())
    return core_server._state


def test_core_speaking_the_pinned_vocabulary_is_ready(monkeypatch) -> None:
    monkeypatch.setattr(core_server, "_state", {})
    expected = core_server.CORE_PIN["vocabulary_version"]
    state = _mark_ready_against(
        httpx.Response(200, json={"vocabulary_version": expected}),
        core_path=None,
        spawned=True,
    )
    assert state["status"] == "ready"


def test_core_speaking_another_vocabulary_is_an_error(monkeypatch) -> None:
    # The vendored Workbench would refuse it with a banner it can't dismiss;
    # reporting it here gives the frontend and chat tools a real reason.
    monkeypatch.setattr(core_server, "_state", {})
    state = _mark_ready_against(
        httpx.Response(200, json={"vocabulary_version": "999"}),
        core_path="/src/goldilocks-core",
        spawned=True,
    )
    assert state["status"] == "error"
    assert "vocabulary_version 999" in state["detail"]
    assert core_server.CORE_PIN["tag"] in state["detail"]
    assert "/src/goldilocks-core" in state["detail"]


def test_mismatch_on_an_already_running_core_says_to_stop_it(monkeypatch) -> None:
    monkeypatch.setattr(core_server, "_state", {})
    state = _mark_ready_against(
        httpx.Response(200, json={"vocabulary_version": "999"}),
        core_path=None,
        spawned=False,
    )
    assert state["status"] == "error"
    assert "already serving" in state["detail"]


def test_unreadable_capabilities_leaves_the_error_to_the_workbench(
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_server, "_state", {})
    state = _mark_ready_against(
        httpx.Response(500, text="Internal Server Error"),
        core_path=None,
        spawned=True,
    )
    assert state["status"] == "ready"


@pytest.mark.parametrize(
    ("response", "health"),
    [
        (httpx.Response(200, json={"status": "ok"}), "core"),
        # e.g. `python -m http.server 8000` -- not a goldilocks-core.
        (httpx.Response(404, text="<html>File not found</html>"), "other"),
        (httpx.Response(200, text="<html>directory listing</html>"), "other"),
        (httpx.Response(503, json={"status": "ok"}), "other"),
    ],
)
def test_only_cores_own_health_answer_counts_as_core(response, health) -> None:
    async def run() -> str:
        async with httpx.AsyncClient(
            base_url=core_server.CORE_SERVER_BASE_URL,
            transport=httpx.MockTransport(lambda request: response),
        ) as client:
            return await core_server._health(client)

    assert asyncio.run(run()) == health


def test_nothing_listening_is_no_health_answer() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def run() -> str:
        async with httpx.AsyncClient(
            base_url=core_server.CORE_SERVER_BASE_URL,
            transport=httpx.MockTransport(refuse),
        ) as client:
            return await core_server._health(client)

    assert asyncio.run(run()) == "none"


def test_a_spawned_core_with_the_wrong_vocabulary_is_stopped(monkeypatch) -> None:
    # Otherwise a retry finds the same stale process still answering, and
    # fixing the checkout can't take effect until goldilocks-agent restarts.
    stopped = []
    monkeypatch.setattr(core_server, "shutdown", lambda: stopped.append(True))
    monkeypatch.setattr(core_server, "_state", {})
    wrong = httpx.Response(200, json={"vocabulary_version": "999"})

    _mark_ready_against(wrong, core_path="/src/goldilocks-core", spawned=True)
    assert stopped == [True]

    stopped.clear()
    _mark_ready_against(wrong, core_path=None, spawned=False)
    assert stopped == []  # someone else's server -- never ours to stop


@pytest.fixture
def configured(monkeypatch):
    """Checkout mode configured, with `_bring_up` replaced by `bring_up`."""
    monkeypatch.setenv("GOLDILOCKS_CORE_PATH", "/opt/goldilocks-core")
    monkeypatch.setattr(core_server, "_task", None)

    def install(bring_up, state):
        monkeypatch.setattr(core_server, "_bring_up", bring_up)
        monkeypatch.setattr(core_server, "_state", state)

    return install


def test_a_failed_start_is_reported_before_it_is_retried(
    monkeypatch, configured
) -> None:
    # The frontend polls every second and stops on "error"; retrying on the
    # very next poll used to hide every failure behind "starting".
    starts = []

    async def bring_up(core_path):
        starts.append(core_path)

    configured(bring_up, {"status": "error", "base_url": None, "detail": "boom"})
    monkeypatch.setattr(core_server, "_failed_at", time.monotonic())

    async def run():
        within_window = core_server.ensure_running()
        monkeypatch.setattr(
            core_server, "_failed_at", time.monotonic() - core_server._RETRY_AFTER_ERROR
        )
        after_window = core_server.ensure_running()
        await asyncio.sleep(0)
        return within_window, after_window

    within_window, after_window = asyncio.run(run())

    assert within_window == {"status": "error", "base_url": None, "detail": "boom"}
    assert after_window["status"] == "starting"
    assert starts == ["/opt/goldilocks-core"]


def test_a_crashed_start_is_reported_not_left_starting(configured) -> None:
    async def bring_up(core_path):
        raise RuntimeError("unexpected")

    configured(bring_up, {"status": "idle", "base_url": None, "detail": None})

    async def run():
        first = core_server.ensure_running()
        await asyncio.sleep(0)
        return first, core_server.ensure_running()

    first, after_crash = asyncio.run(run())

    assert first["status"] == "starting"
    assert after_crash["status"] == "error"
    assert "RuntimeError('unexpected')" in after_crash["detail"]


def test_a_hung_start_is_stopped_before_it_is_reported(monkeypatch) -> None:
    # Left running, it would still hold port 8000 or its asset downloads when
    # the retry spawns another.
    class _Running:
        returncode = None

        def poll(self):
            return None

    async def nothing_answers(client):
        return "none"

    stopped = []
    monkeypatch.setattr(core_server, "_health", nothing_answers)
    monkeypatch.setattr(core_server, "_spawn", lambda core_path: _Running())
    monkeypatch.setattr(core_server, "_process", None)  # restored afterwards
    monkeypatch.setattr(core_server, "shutdown", lambda: stopped.append(True))
    monkeypatch.setattr(core_server, "_STARTUP_TIMEOUT", 0.01)
    monkeypatch.setattr(core_server, "_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(core_server, "_state", {})
    core_server._recent_output.clear()
    core_server._recent_output.append("Installing pseudopotentials/pseudodojo")

    asyncio.run(core_server._bring_up("/opt/goldilocks-core"))

    assert stopped == [True]
    assert core_server._state["status"] == "error"
    assert "so it was stopped" in core_server._state["detail"]
    assert "Installing pseudopotentials/pseudodojo" in core_server._state["detail"]


def test_starting_reports_cores_latest_output(monkeypatch, configured) -> None:
    # A first start can take many minutes; the panel shows core's latest
    # output instead of the last failure's detail.
    async def bring_up(core_path):
        await asyncio.sleep(1)

    configured(bring_up, {"status": "idle", "base_url": None, "detail": "old error"})
    core_server._recent_output.clear()

    async def run():
        first = core_server.ensure_running()
        core_server._recent_output.extend(["Resolved 120 packages", "", ""])
        second = core_server.ensure_running()
        core_server._task.cancel()
        return first, second

    first, second = asyncio.run(run())

    assert first == {"status": "starting", "base_url": None, "detail": None}
    assert second["detail"] == "Resolved 120 packages"
