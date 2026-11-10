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
   (``uvx --from goldilocks-core[http]==<release> goldilocks serve http``,
   2026-09-24; pinned to `core_pin.json`'s release since 2026-10-26, see
   `CORE_PIN`).

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
subprocess the first time the frontend asks for it in this goldilocks-agent
server process's lifetime -- opening DFT Workbench (inline or full-page), a
chat DFT tool, or a page load restoring a saved DFT Workbench state -- never
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
import collections
import contextlib
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from importlib import resources
from typing import TextIO

import httpx

from goldilocks_agent.config import read_core_autostart_enabled, read_core_path

logger = logging.getLogger(__name__)

# The one goldilocks-core release this build runs (`core_pin.json`): the
# Dockerfile fetches its `commit`, PyPI mode installs its `version`, and the
# vendored `goldilocks-workbench` tarball must speak its
# `vocabulary_version` -- the tarball's `HttpCoreClient` refuses any core
# whose `/capabilities` reports a different one, so these can only move
# together (`scripts/bump_core.py` moves them). `tests/test_core_pin.py`
# checks every site against this file.
CORE_PIN: dict = json.loads(
    resources.files("goldilocks_agent").joinpath("core_pin.json").read_text("utf-8")
)

# Never a bare `goldilocks-core[http]`: that would pick up whatever release
# is newest on PyPI and break DFT Workbench for every fresh install.
CORE_PYPI_REQUIREMENT = f"goldilocks-core[http]=={CORE_PIN['version']}"

# Matches `goldilocks-core`'s own `poe serve` task (127.0.0.1:8000). Not
# user-configurable: this is goldilocks-core's own hardcoded default
# (`goldilocks serve http --host 127.0.0.1 --port 8000`).
CORE_SERVER_BASE_URL = "http://127.0.0.1:8000"

# The embedded `goldilocks-workbench` frontend's `HttpCoreClient` calls
# these paths relative to its own origin -- fine in dev, where
# `app/vite.config.js` proxies each one to `CORE_SERVER_BASE_URL` (mirroring
# `goldilocks-core/web`'s own `vite.config.ts`, the canonical list this is
# kept in sync with -- confirmed live 2026-09-25 that a missing entry there
# silently breaks that one feature, e.g. `/magnetic-orderings` once). In a
# built/production deployment there is no dev server to do that proxying,
# so `server.py`'s own routes forward these instead -- same list, same
# target, just a real HTTP hop instead of Vite's dev-only one. Confirmed
# live 2026-09-25 that without this, every one of these calls 404s/405s
# against goldilocks-agent's own StaticFiles-served SPA instead of ever
# reaching core -- DFT Workbench (and everything downstream: mMACE,
# magnetic orderings, bundle downloads) is completely broken in Docker/any
# built deployment without it, not just the one feature missing a proxy
# entry the way the dev-only bug was.
#
# Deliberately excludes `/openapi.json`, unlike the dev-proxy lists above:
# goldilocks-agent's own FastAPI app already serves its *own* schema at
# that exact path (auto-registered at app construction, wins over anything
# added later) -- confirmed live 2026-09-25 that proxying it too just
# shadows nothing (the earlier-registered route always wins) while adding
# route-name collisions for no benefit. `HttpCoreClient` never fetches this
# at runtime anyway (`coreClient.ts`'s own comment: it's a codegen input
# for generating the TS client's types, a dev-time step against core's own
# dev server, not something the running app calls).
#
# The `/magnetic-orderings/rank` pair is goldilocks-core 0.3.1's ranking job
# (goldilocks-core#261): POST submits one (202 with its queue position), GET
# polls it, DELETE cancels it. `POST /magnetic-orderings` only lists from
# 0.3.1 on, so these must ship with that core.
CORE_PROXIED_PATHS = (
    "/capabilities",
    "/explain",
    "/run",
    "/inspect",
    "/magnetic-orderings",
    "/magnetic-orderings/rank",
    "/magnetic-orderings/rank/{job_id}",
    "/health",
    "/ready",
)

# `/health` (verified live 2026-09-24 against a real goldilocks-core
# checkout): `{"status": "ok"}` the instant the FastAPI app itself is
# serving -- unlike `/ready`, it doesn't wait on ML asset installs, and by
# construction of both spawn commands (`goldilocks assets install
# all` runs to completion *before* `goldilocks serve http` starts
# listening at all, see `_spawn_command()`), assets are always already
# installed by the time this answers. Cheap enough to use both to detect an
# already-running instance before spawning, and to poll a freshly spawned
# one until it's up.
_HEALTH_PATH = "/health"
_HEALTH_TIMEOUT = 2.0
# A start with core's environment and assets in place answers in seconds.
# The first start -- ever, or after a core upgrade -- first syncs core's
# environment and runs `goldilocks assets install all`, which fetches
# whatever models and pseudopotential tables are missing or changed from
# PSDI: at goldilocks-core v0.3.0 the PseudoDojo tables (~60 MB) took 13
# minutes and the mMACE backbone (84 MB) up to about 7 on slow links. The
# spawned chain exits non-zero when a step fails, which `_bring_up()`
# reports at once, so this cap only ends a start that hangs.
_STARTUP_TIMEOUT = 3600.0
_POLL_INTERVAL = 0.5
# `/capabilities` is checked once after `/health` passes, for the
# vocabulary the vendored Workbench needs (`_mark_ready()`).
_CAPABILITIES_TIMEOUT = 30.0

# The spawned core's combined stdout/stderr goes to this process's own
# stderr, prefixed (`_forward_output()`) -- i.e. the terminal or `docker
# logs`, where a core 500's traceback can actually be read. The last lines
# are also kept for `_bring_up()`'s error detail.
_OUTPUT_PREFIX = "[goldilocks-core] "
_recent_output: collections.deque[str] = collections.deque(maxlen=20)

# How long a failed start is reported to every caller before the next call
# retries it. The frontend polls `/api/core-server/ensure` every second and
# stops on "error"; retrying on the very next call used to turn every
# failure straight back into "starting" (and a respawn per poll), so the
# error never reached the UI.
_RETRY_AFTER_ERROR = 10.0

_process: subprocess.Popen | None = None
_task: asyncio.Task | None = None
_state: dict = {"status": "idle", "base_url": None, "detail": None}
_failed_at = 0.0  # time.monotonic() of the last failure


async def _health(client: httpx.AsyncClient) -> str:
    """What answers `/health` at `CORE_SERVER_BASE_URL`: "core" for
    goldilocks-core's own `{"status": "ok"}`, "other" for any other HTTP
    answer (another dev server on the port, say), "none" when nothing does.
    A heuristic on the response body -- it used to accept any status under
    500, which reported an unrelated server as a ready core."""
    try:
        response = await client.get(_HEALTH_PATH, timeout=_HEALTH_TIMEOUT)
    except httpx.HTTPError:
        return "none"
    try:
        payload = response.json() if response.status_code == 200 else None
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("status") == "ok":
        return "core"
    return "other"


def _spawn_command(core_path: str | None) -> list[str]:
    """Checkout mode (`core_path` set): `uv run --directory <core_path>
    --extra http poe serve` -- verified live 2026-09-24 against a real
    goldilocks-core checkout: installs/confirms its own ML/pseudopotential
    assets, then serves on :8000. `--extra http` is required, not optional:
    `uv run`'s own implicit sync only resolves the *default* dependency set,
    never optional extras, so a fresh checkout that was never manually
    `uv sync --extra http`'d first fails with `ImportError: The HTTP
    transport requires goldilocks-core[http]` -- reproduced firsthand
    2026-09-25. `poe serve`'s own shell script already refuses to start
    (exit 1) if port 8000 is already answering -- `ensure_running()`
    health-checks first precisely to avoid ever hitting that path, not to
    work around it.

    PyPI mode (`core_path` is ``None``): `uvx --from <CORE_PYPI_REQUIREMENT>
    goldilocks serve http --host 127.0.0.1 --port 8000` -- verified live
    2026-09-24 against the real published package, no checkout anywhere on
    disk -- preceded by the same `goldilocks assets install all` that
    `poe serve` runs: the Workbench sends `fetch_missing: false`, so without
    it a machine that never installed core's assets gets HTTP 424
    (`asset_not_installed`) from every `/explain`. `exec` hands the shell's
    PID to the server itself. Deliberately doesn't pass `--static-root`:
    goldilocks-agent embeds the Workbench UI itself via the
    `goldilocks-workbench` npm package, so this process only needs to serve
    the JSON API, never core's own standalone frontend build.

    Either mode runs mMACE with nothing added here: since goldilocks-core
    v0.3.0 its `mace-torch` is an ordinary dependency and its backbone part
    of the `workbench` assets.
    """
    if core_path:
        return [
            "uv",
            "run",
            "--directory",
            core_path,
            "--extra",
            "http",
            "poe",
            "serve",
        ]
    uvx = f"uvx --from {shlex.quote(CORE_PYPI_REQUIREMENT)} goldilocks"
    return [
        "sh",
        "-c",
        # The profile is `all` from core 0.3.1 on (core#277 renamed it from
        # `workbench`, no alias); it prints `id@version: installed` per asset,
        # which `ensure_running`'s status line picks up during a first start.
        f"{uvx} assets install all && "
        f"exec {uvx} serve http --host 127.0.0.1 --port 8000",
    ]


def _spawn(core_path: str | None) -> subprocess.Popen:
    """`start_new_session=True`: this subprocess (and whatever it
    execs/forks internally -- both `uv run poe serve` and `uvx ... `
    are themselves multi-process chains) is detached into its own
    session, so it isn't tied to this server process's controlling
    terminal/process group, and its PID is also its process-group id --
    `shutdown()` signals that whole group, since the top-level PID is a
    shell while an earlier step of an `sh -c` chain runs.

    stdout/stderr: one pipe, copied to this process's own stderr by
    `_forward_output()` in a daemon thread (`PYTHONUNBUFFERED` so lines
    arrive as they're written, not when a buffer fills).

    `env`: this process's own environment, inherited as-is. A
    `GOLDILOCKS_MACE_BACKBONE` set there overrides the backbone core installs
    with its assets; one still naming the v1.0 checkpoint makes every
    `is_magnetic` request fail (goldilocks-core's docs/mmace-setup.md).
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    _recent_output.clear()
    process = subprocess.Popen(
        _spawn_command(core_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env=env,
    )
    threading.Thread(
        target=_forward_output, args=(process.stdout,), daemon=True
    ).start()
    return process


def _forward_output(stream: TextIO) -> None:
    """Copies the spawned core's output to this process's stderr, prefixed,
    keeping the last lines in `_recent_output`. Runs until the pipe closes,
    i.e. until every process in the spawned chain has exited."""
    for line in stream:
        _recent_output.append(line.rstrip())
        sys.stderr.write(_OUTPUT_PREFIX + line)
    stream.close()


def _last_output_line() -> str | None:
    """The spawned core's last non-blank output line, if any. The full
    output is in this process's own stderr."""
    lines = [line for line in tuple(_recent_output) if line.strip()]
    return lines[-1] if lines else None


def _with_last_output(detail: str) -> str:
    """`detail` plus the spawned core's last non-blank output line, if any."""
    last = _last_output_line()
    return f"{detail} Last output: {last}" if last else detail


def _fail(detail: str) -> None:
    """Records a failed start: logs it, sets `_state` to error with
    `detail`, and starts `_RETRY_AFTER_ERROR`'s window."""
    global _state, _failed_at
    logger.error(detail)
    _state = {"status": "error", "base_url": None, "detail": detail}
    _failed_at = time.monotonic()


async def _served_vocabulary(client: httpx.AsyncClient) -> str | None:
    """The `vocabulary_version` the core at `CORE_SERVER_BASE_URL` reports in
    `/capabilities`, or None if that can't be read -- the embedded Workbench
    then reports the failure itself."""
    try:
        response = await client.get("/capabilities", timeout=_CAPABILITIES_TIMEOUT)
        payload = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("vocabulary_version") is not None:
        return str(payload["vocabulary_version"])
    return None


async def _mark_ready(
    client: httpx.AsyncClient, core_path: str | None, *, spawned: bool
) -> None:
    """Sets `_state` to ready, unless the core now answering speaks a
    different vocabulary than `CORE_PIN` -- which the vendored Workbench
    would refuse with a banner that can't be dismissed. That becomes an error
    naming both versions and the likely fix instead, which the frontend and
    its chat tools relay. A core this module spawned is stopped first, so the
    next attempt starts from the fixed checkout instead of finding the stale
    process still answering. `spawned`: whether the answering core is the
    process this module started (vs. one that was already running)."""
    global _state
    served = await _served_vocabulary(client)
    expected = CORE_PIN["vocabulary_version"]
    if served is None or served == expected:
        _state = {"status": "ready", "base_url": CORE_SERVER_BASE_URL, "detail": None}
        return
    if not spawned:
        fix = (
            f"another goldilocks-core is already serving {CORE_SERVER_BASE_URL}; "
            f"stop it so this build can start its own ({CORE_PIN['tag']})"
        )
    elif core_path:
        fix = f"check out {CORE_PIN['tag']} in GOLDILOCKS_CORE_PATH ({core_path})"
    else:
        fix = (
            f"{CORE_PYPI_REQUIREMENT} doesn't match core_pin.json's "
            "vocabulary_version, so the pin itself is wrong"
        )
    if spawned:
        await asyncio.to_thread(shutdown)
    _fail(
        f"goldilocks-core speaks vocabulary_version {served}, but this build's "
        f"DFT Workbench needs {expected} -- {fix}."
    )


def shutdown() -> None:
    """Never leave an orphaned core server running after this process
    quits. Only ever touches a subprocess *this module* spawned -- an
    instance the user started themselves (`uv run poe serve` in another
    terminal, reused via the health-check in `_bring_up`) is never ours to
    kill. Registered both with `atexit` (normal interpreter exit, e.g.
    uvicorn's own graceful SIGINT/SIGTERM handling) and called explicitly
    from `server.py`'s lifespan shutdown phase -- belt and suspenders, and
    idempotent either way. Signals the spawned process's whole group (it is
    its own session, so its PID is the group id): while an earlier step of an
    `sh -c` chain runs, the top-level PID is the shell, and signalling only
    that would orphan the step."""
    global _process
    if _process is None or _process.poll() is not None:
        return
    logger.info("Shutting down the goldilocks-core process this module spawned")
    _signal_group(_process, signal.SIGTERM)
    try:
        _process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_group(_process, signal.SIGKILL)
        _process.wait(timeout=5)
    _process = None


def _signal_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


atexit.register(shutdown)


async def _bring_up(core_path: str | None) -> None:
    """Background body of `ensure_running()`'s spawn -- runs at most once
    per process (guarded by `_task` in `ensure_running`). `core_path` is
    ``None`` for PyPI mode, a checkout path for checkout mode -- see
    `_spawn_command()`."""
    global _process
    async with httpx.AsyncClient(base_url=CORE_SERVER_BASE_URL) as client:
        health = await _health(client)
        if health == "core":
            # Already running -- the user started it themselves, or a
            # previous call already brought it up. Reuse it: spawning a
            # second instance would just fail (both spawn modes' own
            # servers refuse to bind an already-used port).
            logger.info(
                "goldilocks-core already answering at %s -- reusing it",
                CORE_SERVER_BASE_URL,
            )
            ours = _process is not None and _process.poll() is None
            await _mark_ready(client, core_path, spawned=ours)
            return
        if health == "other":
            # Spawning would only fail to bind -- after a full `uv sync` or
            # asset install in PyPI mode, with a less telling message.
            _fail(
                f"{CORE_SERVER_BASE_URL} is answering, but not as goldilocks-core "
                "-- stop whatever is using port 8000, then reopen DFT Workbench."
            )
            return

        logger.info("Starting goldilocks-core: %s", " ".join(_spawn_command(core_path)))
        try:
            _process = _spawn(core_path)
        except OSError as exc:
            _fail(f"Failed to spawn goldilocks-core: {exc}")
            return

        deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            if await _health(client) == "core":
                logger.info("goldilocks-core is up at %s", CORE_SERVER_BASE_URL)
                await _mark_ready(client, core_path, spawned=True)
                return
            if _process.poll() is not None:
                hint = (
                    "check GOLDILOCKS_CORE_PATH and that "
                    "`uv run --directory <path> --extra http poe serve` "
                    "works on its own"
                    if core_path
                    else "check that "
                    f"`uvx --from {CORE_PYPI_REQUIREMENT} goldilocks serve http` "
                    "works on its own"
                )
                _fail(
                    _with_last_output(
                        f"goldilocks-core exited (code {_process.returncode}) "
                        f"before it started answering -- {hint}."
                    )
                )
                return
            await asyncio.sleep(_POLL_INTERVAL)

    # Stopped first: left running, the hung start would still hold port 8000
    # or its asset downloads when the retry spawns another.
    detail = _with_last_output(
        f"goldilocks-core did not answer at {CORE_SERVER_BASE_URL}{_HEALTH_PATH} "
        f"within {_STARTUP_TIMEOUT:.0f}s of starting, so it was stopped."
    )
    await asyncio.to_thread(shutdown)
    _fail(detail)


def ensure_running() -> dict:
    """Idempotent, non-blocking: safe to call every time the frontend opens
    DFT Workbench. Kicks off the spawn/health-check exactly once per
    process if needed, and always returns immediately with the
    current status for the caller to relay/poll:

    - ``not_configured``: neither `GOLDILOCKS_CORE_PATH` nor
      `GOLDILOCKS_AGENT_CORE_AUTOSTART` -- nothing to auto-start (the
      hosted-deployment case, or local and simply unset).
    - ``starting``: a spawn/health-check is in flight -- poll again.
      `detail` is the spawned core's latest output line, if any, so a long
      first start (`_STARTUP_TIMEOUT`) shows signs of life; it can stay on
      one line for minutes while `goldilocks assets install` downloads.
    - ``ready``: something is answering at `base_url`, reused or freshly
      spawned.
    - ``error``: configured but the spawn/health-check failed; `detail`
      has a specific reason. Every call for `_RETRY_AFTER_ERROR` seconds
      gets that error; the first call after that retries from scratch (e.g.
      the user fixed their config and reopened DFT Workbench) rather than
      latching the failure forever.
    """
    global _task
    core_path = read_core_path()
    if not core_path and not read_core_autostart_enabled():
        return {"status": "not_configured", "base_url": None, "detail": None}
    task_in_flight = _task is not None and not _task.done()
    if _task is not None and not task_in_flight and _state["status"] == "starting":
        # `_bring_up` sets ready or error on every path it expects, so this
        # is an unexpected crash -- report it rather than "starting" forever.
        reason = "cancelled" if _task.cancelled() else repr(_task.exception())
        _fail(f"Starting goldilocks-core failed unexpectedly: {reason}")
    retry_due = (
        _state["status"] == "error"
        and time.monotonic() - _failed_at >= _RETRY_AFTER_ERROR
    )
    if not task_in_flight and (_state["status"] == "idle" or retry_due):
        _state["status"] = "starting"
        _task = asyncio.create_task(_bring_up(core_path))
    state = dict(_state)
    if state["status"] == "starting":
        state["detail"] = _last_output_line()
    return state
