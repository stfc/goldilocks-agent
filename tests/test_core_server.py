"""`core_server.py`'s spawn-command construction (2026-09-25: `--extra http`
fix plus mMACE's lazy setup, see its own module docstring). Pure Python,
no subprocess/network -- asserting on the *constructed command*, not real
execution, same "which tool/args, not what happens" philosophy the rest of
this test suite already follows for confirmation-gated tools.
"""

from __future__ import annotations

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


def test_pypi_mode_unaffected_by_mmace_setting(monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_MMACE_ENABLED", "1")
    # mMACE can only ever run in checkout mode -- no addressable, persistent
    # venv exists in PyPI/uvx mode to install its fork into.
    assert core_server._spawn_command(None) == [
        "uvx",
        "--from",
        "goldilocks-core[http]",
        "goldilocks",
        "serve",
        "http",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
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
    assert checkpoint in script
    assert "curl -fsSL -o" in script
    assert f"[ -f {checkpoint} ]" in script
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
    # Ordering: checkpoint -> sync (creates the venv) -> manual installs ->
    # actual server start. An explicit `uv sync` wipes the manually-
    # installed packages (verified live this session), so they only survive
    # by installing *after* this specific sync, not before it.
    sync_idx = script.index(sync_call)
    pip_install_idx = script.index("uv pip install")
    serve_idx = script.index("uv run --directory")
    assert checkpoint in script[:sync_idx]
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
            pass

        return _FakeProcess()

    monkeypatch.setattr(core_server.subprocess, "Popen", fake_popen)

    core_server._spawn("/opt/goldilocks-core")

    assert captured["env"]["GOLDILOCKS_MACE_BACKBONE"] == str(mmace_checkpoint_path())
