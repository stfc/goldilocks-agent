"""The agent server's own client for goldilocks-core's HTTP API (#142).

The chat's DFT Workbench tools run on the agent server, so the server talks
to core itself, over the same `goldilocks serve http` that `core_server.py`
starts and pins (`core_pin.json`). It never imports `goldilocks_core`; the
boundary stays a process boundary (AGENTS.md, design doc 14.3).

It speaks to core the way the embedded Workbench's `HttpCoreClient` does:
the same routes and bodies, the same refusal of a non-JSON (or non-zip)
answer, the same archive filename. Failures carry core's `error.kind` --
callers match on the kind, never on the message, which core rewords
(goldilocks-core fork #299) -- plus `network_error`, `http_error` and
`invalid_response` for what never reached core's error envelope.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from goldilocks_agent import core_server

# How long `ready` waits for a core that is still starting. A first start
# after an upgrade can download assets for longer (`core_server`'s own
# timeout is an hour); a chat turn reports that instead of waiting it out.
_READY_TIMEOUT_S = 120.0
_READY_POLL_S = 1.0

# Generous: `/explain` with mMACE's is_magnetic on a large cell, or `/run`
# writing a bundle, can take tens of seconds.
_REQUEST_TIMEOUT_S = 300.0


class CoreFailure(Exception):
    """A goldilocks-core request that failed: core's own `error.kind` and
    message, or `network_error` / `http_error` / `invalid_response` /
    `core_unavailable` for what never got core's error envelope."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class Archive:
    """A generated input bundle: the zip's bytes and the filename the
    Workbench would save it under."""

    content: bytes
    filename: str


def archive_filename(structure_name: str, task: str) -> str:
    """`<structure>-<task>.zip`, as the Workbench names a bundle."""
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", structure_name)
    return f"{safe}-{task}.zip" if safe else f"goldilocks-{task}.zip"


class CoreApi:
    """goldilocks-core's HTTP API, for one agent server process."""

    def __init__(
        self,
        base_url: str = core_server.CORE_SERVER_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ensure_running: Callable[[], dict] = core_server.ensure_running,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=_REQUEST_TIMEOUT_S
        )
        self._ensure_running = ensure_running
        self._capabilities: dict | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ready(
        self,
        *,
        timeout_s: float = _READY_TIMEOUT_S,
        poll_s: float = _READY_POLL_S,
        on_wait: Callable[[str | None], None] | None = None,
    ) -> None:
        """Returns once core answers, starting it if this deployment starts
        it (`core_server.ensure_running`). `on_wait` gets core's latest
        startup line while it starts. Raises `CoreFailure`
        (`core_unavailable`) if it failed to start or is still starting
        after `timeout_s`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            state = self._ensure_running()
            # Nothing to start here: core is someone else's service, at the
            # same address the panel's proxy uses.
            if state["status"] in ("ready", "not_configured"):
                return
            if state["status"] == "error":
                raise CoreFailure(
                    "core_unavailable",
                    f"goldilocks-core couldn't start: {state.get('detail')}",
                )
            if loop.time() >= deadline:
                raise CoreFailure(
                    "core_unavailable",
                    "goldilocks-core is still starting (a first start can take "
                    "a while as it installs its assets); try again shortly.",
                )
            if on_wait is not None:
                on_wait(state.get("detail"))
            await asyncio.sleep(poll_s)

    async def capabilities(self) -> dict:
        """`/capabilities`, fetched once per process. Refuses a core whose
        vocabulary isn't the one this build pins (`core_pin.json`): the
        setting keys and values the tools use belong to that vocabulary."""
        if self._capabilities is None:
            caps = await self._json("GET", "/capabilities")
            pinned = core_server.CORE_PIN["vocabulary_version"]
            served = caps.get("vocabulary_version")
            if served != pinned:
                raise CoreFailure(
                    "vocabulary_mismatch",
                    f"goldilocks-core speaks vocabulary {served!r}, but this "
                    f"build is pinned to {pinned!r}.",
                )
            self._capabilities = caps
        return self._capabilities

    async def inspect(self, structure: dict) -> dict:
        """`/inspect` for one structure (`structure_content`,
        `structure_name`, `structure_format`)."""
        return _schema_v1(await self._json("POST", "/inspect", structure))

    async def explain(self, request: dict) -> dict:
        """`/explain`: the advice-only preview; it still answers when `/run`
        would refuse, with `blocking` listing why."""
        return await self._json("POST", "/explain", request)

    async def run_archive(self, request: dict) -> Archive:
        """`/run` as a zip: the generated input bundle."""
        response = await self._send(
            "POST",
            "/run",
            {**request, "respond_with": "archive"},
            accept="application/zip",
        )
        if not _content_type(response).startswith("application/zip"):
            raise _invalid(response.status_code, "returned an invalid archive response")
        return Archive(
            content=response.content,
            filename=archive_filename(request["structure_name"], request["task"]),
        )

    async def magnetic_orderings(self, request: dict) -> dict:
        """`/magnetic-orderings`: the candidate orderings, unranked."""
        return await self._json("POST", "/magnetic-orderings", request)

    async def submit_ranking(self, request: dict) -> dict:
        """Queues an mMACE ranking of the candidates; returns the job's
        status (`{job_id, state, position}`). A job nobody polls for 120 s
        is dropped by core."""
        return await self._json("POST", "/magnetic-orderings/rank", request)

    async def ranking_status(self, job_id: str) -> dict:
        """A ranking job's status. A job core no longer holds (cancelled,
        long finished, or from before a restart) fails `unknown_ranking`."""
        return await self._json(
            "GET", f"/magnetic-orderings/rank/{quote(job_id, safe='')}"
        )

    async def cancel_ranking(self, job_id: str) -> None:
        """Drops a ranking job, killing its worker if it runs. A job core
        doesn't hold any more is already gone, so that isn't a failure."""
        try:
            await self._send(
                "DELETE", f"/magnetic-orderings/rank/{quote(job_id, safe='')}"
            )
        except CoreFailure as failure:
            if failure.kind != "unknown_ranking":
                raise

    async def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        response = await self._send(method, path, body)
        if not _content_type(response).startswith("application/json"):
            raise _invalid(response.status_code, "returned an invalid JSON response")
        try:
            return response.json()
        except ValueError as exc:
            raise _invalid(response.status_code, "returned unreadable JSON") from exc

    async def _send(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        accept: str = "application/json",
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, path, json=body, headers={"Accept": accept}
            )
        except httpx.HTTPError as exc:
            raise CoreFailure(
                "network_error", f"Cannot reach goldilocks-core: {exc}"
            ) from exc
        if response.is_success:
            return response
        try:
            envelope = response.json()
        except ValueError:
            envelope = None
        error = envelope.get("error") if isinstance(envelope, dict) else None
        if isinstance(error, dict) and isinstance(error.get("kind"), str):
            raise CoreFailure(
                error["kind"],
                str(error.get("message", "")),
                status=response.status_code,
                details=error.get("details") or {},
            )
        raise CoreFailure(
            "http_error",
            f"goldilocks-core rejected the request ({response.status_code}).",
            status=response.status_code,
        )


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").lower()


def _invalid(status: int, what: str) -> CoreFailure:
    return CoreFailure("invalid_response", f"goldilocks-core {what}.", status=status)


def _schema_v1(body: dict) -> dict:
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        raise CoreFailure(
            "invalid_response",
            "goldilocks-core returned an incompatible schema version.",
        )
    return body
