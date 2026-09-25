"""Auto-starts goldilocks-core's own HTTP backend (`goldilocks serve http`)
for the DFT Workbench Tool's embedded `goldilocks-workbench` frontend to
talk to -- local/desktop deployment only (design doc 19): a hosted
deployment would already have core's Workbench running as its own
separately-managed service, so both `read_core_path()` and
`read_core_autostart_enabled()` being unset there just means "nothing to
auto-start," not an error.

Two ways to source goldilocks-core, checked in this order:

1. ``read_core_path()`` set -- someone actively developing core itself:
   runs *that checkout's* ``uv run --directory <path> poe serve``, picking
   up local, uncommitted changes.
2. Otherwise, if ``read_core_autostart_enabled()`` -- everyone else: runs
   the real published PyPI package directly, no checkout needed at all
   (``uvx --from goldilocks-core[http] goldilocks serve http``, 2026-09-24).

Either way this is a real, separate OS process, deliberately never an
in-process import: goldilocks-agent treats goldilocks-core as an external
tool it shells out to, the same way `mlip-cli` treats `janus-core`, never a
Python dependency of its own package. A proposal to merge the two packages
into one uv workspace (junwen94/goldilocks-core#75) was raised and rejected
specifically because it would make `import goldilocks_core` mechanically
possible from this package's own environment -- goldilocks-core landing on
PyPI doesn't reopen that question, it only changes *how* the external
subprocess gets sourced (a checkout path vs. an installable package).

Lazy, singleton, process-lifetime: `ensure_running()` only ever spawns a
subprocess the first time the frontend actually opens DFT Workbench (inline
or full-page) in this goldilocks-agent server process's lifetime -- never
eagerly at import/startup. Once spawned it outlives any one browser
tab/view; only this process exiting tears it down (`shutdown()`, registered
with `atexit` -- uvicorn's normal SIGINT/SIGTERM handling exits the
interpreter cleanly, which is what actually fires atexit callbacks; nothing
here handles a hard `kill -9`, same limitation any atexit-based cleanup has).

Non-blocking by design: `ensure_running()` never awaits the spawn+health-poll
itself -- it kicks that off as a background task at most once and returns
immediately with whatever the current status is, so `server.py`'s route can
be polled cheaply and repeatedly by the frontend (design: show a "starting
up..." state, not block one HTTP request for up to `_STARTUP_TIMEOUT`
seconds). Real invocation of both spawn modes, and both the fresh-spawn and
already-running-reuse paths, were exercised manually against real
goldilocks-core (both the checkout and the published PyPI package, 2026-09-24) before
writing this -- see the module-level constants' comments for what was
actually verified.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shlex
import subprocess

import httpx

from goldilocks_agent.config import (
    mmace_checkpoint_path,
    read_core_autostart_enabled,
    read_core_path,
    read_mmace_enabled,
)

logger = logging.getLogger(__name__)

# mMACE's fork (see README.md's Configuration section for the full manual
# recipe this mirrors) has no PyPI release -- PyPI itself rejects a git
# dependency in a published package's metadata, so this can never become a
# `uv sync` extra, only an explicit `uv pip install` chained ahead of
# `poe serve` when `read_mmace_enabled()` is set.
_MMACE_CHECKPOINT_URL = (
    "https://data-collections.psdi.ac.uk/api/records/1g8rw-q8128/files/"
    "mace_matpes_pbe_baseline_run-3.model/content"
)
_MMACE_FORK_SPEC = (
    "mace-torch @ git+https://github.com/CheukHinHoJerry/mace.git"
    "@19cdf6692c48e068a24e06cfe1ffc670e8aea3dd"
)

# Matches `goldilocks-core`'s own `poe serve` task (127.0.0.1:8000) and
# `app/vite.config.js`'s dev-proxy target for `/capabilities`, `/explain`,
# `/run`, `/inspect`, `/health`, `/ready`, `/openapi.json` -- all forwarded
# to this same origin. Not user-configurable: this is goldilocks-core's own
# hardcoded default (`goldilocks serve http --host 127.0.0.1 --port 8000`),
# same value hardcoded on the frontend's side of that proxy.
CORE_SERVER_BASE_URL = "http://127.0.0.1:8000"

# `/health` (verified live 2026-09-24 against a real goldilocks-core
# checkout): `{"status": "ok"}` the instant the FastAPI app itself is
# serving -- unlike `/ready`, it doesn't wait on ML asset installs, and by
# construction of `poe serve`'s own shell script (`goldilocks assets
# install workbench` runs to completion *before* `exec goldilocks serve
# http` starts listening at all), assets are always already installed by
# the time this answers. Cheap enough to use both to detect an
# already-running instance before spawning, and to poll a freshly spawned
# one until it's up.
_HEALTH_PATH = "/health"
_HEALTH_TIMEOUT = 2.0
# Real measured cold start (assets already cached locally) was ~1s -- this
# budget is generous for a from-scratch machine where `goldilocks assets
# install workbench` still has to download models/pseudopotentials
# (several hundred MB) before the server can start listening at all.
_STARTUP_TIMEOUT = 180.0
# mMACE's first-ever setup chains a lot more before `poe serve` even starts
# listening: goldilocks-core's own deps (torch et al., via `--extra http`'s
# lazy sync), then the manual `ase`/`e3nn`/`sphericart`/mace-torch-fork
# installs, then the ~84MB checkpoint download -- several GB total, same
# order of magnitude as MLIP Playground's own `_TIMEOUT = 300.0` "cold
# MACE-MP model load plus uv sync" budget, but with more steps chained in
# sequence, hence more headroom.
_STARTUP_TIMEOUT_MMACE = 900.0
_POLL_INTERVAL = 0.5

_process: subprocess.Popen | None = None
_task: asyncio.Task | None = None
_state: dict = {"status": "idle", "base_url": None, "detail": None}


async def _is_healthy(client: httpx.AsyncClient) -> bool:
    try:
        response = await client.get(_HEALTH_PATH, timeout=_HEALTH_TIMEOUT)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def _spawn_command(core_path: str | None) -> list[str]:
    """Checkout mode (`core_path` set): `uv run --directory <core_path>
    --extra http poe serve` -- verified live 2026-09-24 against a real
    goldilocks-core checkout: installs/confirms its own ML/pseudopotential
    assets, then serves on :8000. `--extra http` is required, not optional:
    `uv run`'s own implicit sync only resolves the *default* dependency set,
    never optional extras, so a fresh checkout that was never manually
    `uv sync --extra http`'d first fails with `ImportError: The HTTP
    transport requires goldilocks-core[http]` -- reproduced firsthand
    2026-09-25 (this bug predates mMACE; it's a real gap in the plain
    checkout-mode path this happened to surface while wiring up mMACE, which
    *requires* checkout mode and so hits it every time). `poe serve`'s own
    shell script already refuses to start (exit 1) if port 8000 is already
    answering -- `ensure_running()` health-checks first precisely to avoid
    ever hitting that path, not to work around it.

    If `read_mmace_enabled()` is also set, the same command is wrapped in
    `sh -c` to chain mMACE's one-time manual setup ahead of it (see
    README.md's Configuration section for the same recipe run by hand):
    download the checkpoint (skipped if already present -- persists across
    restarts via `$HOME`, see `mmace_checkpoint_path()`), then `uv pip
    install` the three packages goldilocks-ml has no PyPI extra for. Order
    matters, verified 2026-09-25: these must run *after* `--extra http`'s
    own sync, since an explicit `uv sync` wipes them, but `uv run`'s own
    implicit sync (left last here, as `poe serve` itself) does not touch
    packages outside the resolved dependency set. Each step is idempotent/
    cache-backed (`uv pip install` of an already-satisfied exact version is
    a fast no-op) -- no hand-rolled "already done" flag needed, same as
    `mlip_cli`'s own `uv run --project` never checking that either.

    PyPI mode (`core_path` is ``None``): `uvx --from goldilocks-core[http]
    goldilocks serve http --host 127.0.0.1 --port 8000` -- verified live
    2026-09-24 against the real published package, no checkout anywhere on
    disk. mMACE can never run this way -- there is no persistent, addressable
    venv to install its fork into (see `read_mmace_enabled()`'s docstring) --
    so `read_mmace_enabled()` is only ever consulted in the `core_path`
    branch above. Deliberately doesn't pass `--static-root`: goldilocks-agent
    embeds the Workbench UI itself via the `goldilocks-workbench` npm
    package, so this process only needs to serve the JSON API, never core's
    own standalone frontend build.
    """
    if core_path:
        serve_cmd = [
            "uv",
            "run",
            "--directory",
            core_path,
            "--extra",
            "http",
            "poe",
            "serve",
        ]
        if read_mmace_enabled():
            checkpoint = mmace_checkpoint_path()
            checkpoint_q = shlex.quote(str(checkpoint))
            fork_q = shlex.quote(_MMACE_FORK_SPEC)
            core_path_q = shlex.quote(core_path)
            venv_python_q = shlex.quote(f"{core_path}/.venv/bin/python")
            # Explicit `--python <venv>/bin/python`, not `--project <path>`
            # or a bare `cd <path>` + inherited `VIRTUAL_ENV` -- both
            # confirmed live 2026-09-25 to silently install into
            # goldilocks-agent's own `.venv` instead (real environment
            # pollution, since cleaned up, while reporting success): this
            # whole command is itself a child of `poe serve`'s own `uv run`
            # (`UV_RUN_RECURSION_DEPTH` is already 1 by the time this runs),
            # and neither `--project` nor an overridden `VIRTUAL_ENV` env
            # var actually wins against that nested context -- only naming
            # the target interpreter directly is unambiguous. That target
            # has to actually *exist* first, though: on a brand-new
            # (first-ever) `goldilocks_core_venv` volume, `.venv/bin/python`
            # doesn't exist until something creates it -- confirmed live
            # 2026-09-25 (`error: No virtual environment ... found for path`)
            # -- hence the explicit `uv sync --directory ... --extra http`
            # ahead of the manual installs, not just relying on `poe serve`'s
            # own trailing sync to have created it first.
            script = (
                f"mkdir -p {shlex.quote(str(checkpoint.parent))} && "
                f"[ -f {checkpoint_q} ] || "
                f"curl -fsSL -o {checkpoint_q} {shlex.quote(_MMACE_CHECKPOINT_URL)} && "
                f"uv sync --directory {core_path_q} --extra http && "
                f"uv pip install --python {venv_python_q} ase==3.28.0 e3nn==0.4.4 "
                "sphericart==1.0.9 sphericart-torch==1.0.9 && "
                f"uv pip install --python {venv_python_q} {fork_q} && "
                + shlex.join(serve_cmd)
            )
            return ["sh", "-c", script]
        return serve_cmd
    return [
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


def _spawn(core_path: str | None) -> subprocess.Popen:
    """`start_new_session=True`: this subprocess (and whatever it
    execs/forks internally -- both `uv run poe serve` and `uvx ... `
    are themselves multi-process chains) is detached into its own
    session, so it isn't tied to this server process's controlling
    terminal/process group. Termination is still a plain
    `Popen.terminate()`/`kill()` on this top-level PID (verified live
    2026-09-24 for both spawn modes: SIGTERM to the top-level process
    cleanly took down every descendant, including the actual `goldilocks
    serve http` process several levels down in its own process group) --
    no `os.killpg` needed.

    `env`: merged with (never replacing) this process's own environment --
    `GOLDILOCKS_MACE_BACKBONE` only needs adding when mMACE is enabled, so
    `poe serve`'s own `goldilocks_core.analysis.is_magnetic`/
    `advisors.magnetic_ordering_ml` can find the checkpoint this same
    command just downloaded. Note this process is itself launched via
    `uv run` (`poe serve`'s own `CMD`), which sets `VIRTUAL_ENV` to
    goldilocks-agent's own `.venv` in *this* environment -- inherited as-is,
    that's what made `_spawn_command()`'s mMACE setup need an explicit
    `--python <venv>/bin/python` on its `uv pip install` calls rather than
    relying on `cd`/an overridden `VIRTUAL_ENV` here (confirmed live
    2026-09-25: neither actually wins against the nested `uv run` context).
    """
    env = os.environ.copy()
    if core_path and read_mmace_enabled():
        env["GOLDILOCKS_MACE_BACKBONE"] = str(mmace_checkpoint_path())
    return subprocess.Popen(
        _spawn_command(core_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def shutdown() -> None:
    """Never leave an orphaned core server running after this process
    quits. Only ever touches a subprocess *this module* spawned -- an
    instance the user started themselves (`uv run poe serve` in another
    terminal, reused via the health-check in `_bring_up`) is never ours to
    kill. Registered both with `atexit` (normal interpreter exit, e.g.
    uvicorn's own graceful SIGINT/SIGTERM handling) and called explicitly
    from `server.py`'s lifespan shutdown phase -- belt and suspenders, and
    idempotent either way."""
    global _process
    if _process is None or _process.poll() is not None:
        return
    logger.info("Shutting down the goldilocks-core process this module spawned")
    try:
        _process.terminate()
        _process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _process.kill()
        _process.wait(timeout=5)
    _process = None


atexit.register(shutdown)


async def _bring_up(core_path: str | None) -> None:
    """Background body of `ensure_running()`'s spawn -- runs at most once
    per process (guarded by `_task` in `ensure_running`). `core_path` is
    ``None`` for PyPI mode, a checkout path for checkout mode -- see
    `_spawn_command()`."""
    global _process, _state
    async with httpx.AsyncClient(base_url=CORE_SERVER_BASE_URL) as client:
        if await _is_healthy(client):
            # Already running -- the user started it themselves, or a
            # previous call already brought it up. Reuse it: spawning a
            # second instance would just fail (both spawn modes' own
            # servers refuse to bind an already-used port).
            logger.info(
                "goldilocks-core already answering at %s -- reusing it",
                CORE_SERVER_BASE_URL,
            )
            _state = {
                "status": "ready",
                "base_url": CORE_SERVER_BASE_URL,
                "detail": None,
            }
            return

        logger.info("Starting goldilocks-core: %s", " ".join(_spawn_command(core_path)))
        try:
            _process = _spawn(core_path)
        except OSError as exc:
            logger.error("Failed to spawn goldilocks-core: %s", exc)
            _state = {"status": "error", "base_url": None, "detail": str(exc)}
            return

        startup_timeout = (
            _STARTUP_TIMEOUT_MMACE
            if core_path and read_mmace_enabled()
            else _STARTUP_TIMEOUT
        )
        deadline = asyncio.get_event_loop().time() + startup_timeout
        while asyncio.get_event_loop().time() < deadline:
            if await _is_healthy(client):
                logger.info("goldilocks-core is up at %s", CORE_SERVER_BASE_URL)
                _state = {
                    "status": "ready",
                    "base_url": CORE_SERVER_BASE_URL,
                    "detail": None,
                }
                return
            if _process.poll() is not None:
                hint = (
                    "check GOLDILOCKS_CORE_PATH and that "
                    "`uv run --directory <path> --extra http poe serve` "
                    "works on its own"
                    if core_path
                    else "check that "
                    "`uvx --from goldilocks-core[http] goldilocks serve http` "
                    "works on its own"
                )
                detail = (
                    f"goldilocks-core exited (code {_process.returncode}) "
                    f"before it started answering -- {hint}."
                )
                logger.error(detail)
                _state = {"status": "error", "base_url": None, "detail": detail}
                return
            await asyncio.sleep(_POLL_INTERVAL)

    detail = (
        f"goldilocks-core did not answer at {CORE_SERVER_BASE_URL}{_HEALTH_PATH} "
        f"within {startup_timeout:.0f}s of starting."
    )
    logger.error(detail)
    _state = {"status": "error", "base_url": None, "detail": detail}


def ensure_running() -> dict:
    """Idempotent, non-blocking: safe to call every time the frontend opens
    DFT Workbench. Kicks off the spawn/health-check exactly once per
    process if needed, and always returns immediately with the
    current status for the caller to relay/poll:

    - ``not_configured``: neither `GOLDILOCKS_CORE_PATH` nor
      `GOLDILOCKS_AGENT_CORE_AUTOSTART` -- nothing to auto-start (the
      hosted-deployment case, or local and simply unset).
    - ``starting``: a spawn/health-check is in flight -- poll again.
    - ``ready``: something is answering at `base_url`, reused or freshly
      spawned.
    - ``error``: configured but the spawn/health-check failed; `detail`
      has a specific reason. A later call retries from scratch (e.g. the
      user fixed their config and reopened DFT Workbench) rather than
      latching the failure forever.
    """
    global _task
    core_path = read_core_path()
    if not core_path and not read_core_autostart_enabled():
        return {"status": "not_configured", "base_url": None, "detail": None}
    task_in_flight = _task is not None and not _task.done()
    if not task_in_flight and _state["status"] in ("idle", "error"):
        _state["status"] = "starting"
        _task = asyncio.create_task(_bring_up(core_path))
    return dict(_state)
