"""`core_server.py`'s spawn-command construction (2026-09-25: `--extra http`
fix plus mMACE's lazy setup, see its own module docstring). Pure Python,
no subprocess/network -- asserting on the *constructed command*, not real
execution, same "which tool/args, not what happens" philosophy the rest of
this test suite already follows for confirmation-gated tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import shlex
import time

import httpx
import pytest

from goldilocks_agent import core_server
from goldilocks_agent.config import mmace_checkpoint_path, read_mmace_enabled


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


def test_pypi_mode_installs_assets_then_serves(monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "1")
    # mMACE can only ever run in checkout mode -- no addressable, persistent
    # venv exists in PyPI/uvx mode to install its fork into.
    uvx = f"uvx --from {shlex.quote(core_server.CORE_PYPI_REQUIREMENT)} goldilocks"
    # Assets first, the way checkout mode's `poe serve` does: the Workbench
    # sends fetch_missing=false, so a machine without them gets HTTP 424.
    assert core_server._spawn_command(None) == [
        "sh",
        "-c",
        f"{uvx} assets install workbench && "
        f"exec {uvx} serve http --host 127.0.0.1 --port 8000",
    ]


def test_mmace_enabled_chains_setup_ahead_of_serve(monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "1")

    command = core_server._spawn_command("/opt/goldilocks-core")

    assert command[:2] == ["sh", "-c"]
    script = command[2]
    checkpoint = str(mmace_checkpoint_path())
    venv_python = "/opt/goldilocks-core/.venv/bin/python"
    # Explicit `--python <venv>/bin/python` on both `uv pip install` calls,
    # not `--project <path>` or a bare `cd`/inherited `VIRTUAL_ENV` -- all
    # three confirmed live 2026-09-25 to silently install into
    # goldilocks-agent's own `.venv` instead (this whole command is itself
    # a child of `poe serve`'s own `uv run`, and nothing short of naming the
    # target interpreter directly wins against that nested context).
    assert script.count(f"--python {venv_python}") == 2
    assert "--project" not in script
    # The checkpoint is fetched and verified in Python before the spawn
    # (`_ensure_mmace_checkpoint()`), not by the shell chain.
    assert checkpoint not in script
    assert "curl" not in script
    assert "ase==3.28.0 e3nn==0.4.4 sphericart==1.0.9 sphericart-torch==1.0.9" in script
    assert (
        "mace-torch @ git+https://github.com/CheukHinHoJerry/mace.git"
        "@19cdf6692c48e068a24e06cfe1ffc670e8aea3dd" in script
    )
    # `.venv/bin/python` (the `--python` target above) doesn't exist at all
    # on a brand-new volume until something creates it -- confirmed live
    # 2026-09-25 (`error: No virtual environment ... found for path`) -- so
    # an explicit `uv sync --directory ... --extra http` has to run first,
    # not just rely on the final `uv run`'s own trailing sync to have
    # created it already.
    sync_call = "uv sync --directory /opt/goldilocks-core --extra http"
    assert sync_call in script
    # Ordering: sync (creates the venv) -> manual installs -> actual server
    # start. An explicit `uv sync` wipes the manually-installed packages
    # (verified live 2026-09-25), so they only survive by installing *after*
    # this specific sync, not before it.
    sync_idx = script.index(sync_call)
    pip_install_idx = script.index("uv pip install")
    serve_idx = script.index("uv run --directory")
    assert sync_idx < pip_install_idx < serve_idx
    assert script.rstrip().endswith(
        "uv run --directory /opt/goldilocks-core --extra http poe serve"
    )


def test_read_mmace_enabled_env_var(monkeypatch) -> None:
    monkeypatch.delenv("GOLDILOCKS_AGENT_MMACE_ENABLED", raising=False)
    assert read_mmace_enabled() is False
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "1")
    assert read_mmace_enabled() is True
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "0")
    assert read_mmace_enabled() is False


def test_mmace_checkpoint_path_is_home_relative() -> None:
    path = mmace_checkpoint_path()
    assert path.name == "mace_matpes_pbe_baseline_run-3.model"
    assert ".local/share/goldilocks/mmace" in str(path)


def test_spawn_sets_mace_backbone_env_for_checkout_mode(monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "1")
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]

        class _FakeProcess:
            stdout = io.StringIO("")

        return _FakeProcess()

    monkeypatch.setattr(core_server.subprocess, "Popen", fake_popen)

    core_server._spawn("/opt/goldilocks-core")

    assert captured["env"]["GOLDILOCKS_MACE_BACKBONE"] == str(mmace_checkpoint_path())


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


@pytest.fixture
def fake_checkpoint(tmp_path, monkeypatch):
    """A small stand-in for the 84MB checkpoint: its own size/md5 constants
    and a mock transport serving whatever bytes the test puts in `served`."""
    payload = b"mace checkpoint bytes"
    path = tmp_path / "mmace" / "model.model"
    served = {"body": payload, "requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        served["requests"] += 1
        return httpx.Response(200, content=served["body"])

    real_client = httpx.Client
    monkeypatch.setattr(core_server, "mmace_checkpoint_path", lambda: path)
    monkeypatch.setattr(core_server, "_MMACE_CHECKPOINT_SIZE", len(payload))
    monkeypatch.setattr(
        core_server, "_MMACE_CHECKPOINT_MD5", hashlib.md5(payload).hexdigest()
    )
    monkeypatch.setattr(
        core_server.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return path, payload, served


def test_checkpoint_already_verified_is_not_downloaded_again(fake_checkpoint) -> None:
    path, payload, served = fake_checkpoint
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)

    core_server._ensure_mmace_checkpoint()

    assert served["requests"] == 0


def test_truncated_checkpoint_is_replaced(fake_checkpoint) -> None:
    # An interrupted first download used to leave a partial file that the
    # old `[ -f ckpt ] || curl` chain then accepted forever.
    path, payload, served = fake_checkpoint
    path.parent.mkdir(parents=True)
    path.write_bytes(payload[:5])

    core_server._ensure_mmace_checkpoint()

    assert served["requests"] == 1
    assert path.read_bytes() == payload
    assert not path.with_name(f"{path.name}.part").exists()


def test_checkpoint_download_that_fails_verification_is_discarded(
    fake_checkpoint,
) -> None:
    path, _, served = fake_checkpoint
    served["body"] = b"<html>error page</html>"

    with pytest.raises(core_server._CheckpointMismatch):
        core_server._ensure_mmace_checkpoint()

    assert not path.exists()
    assert not path.with_name(f"{path.name}.part").exists()


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
