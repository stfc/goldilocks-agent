"""Local HTTP bridge between the web UI and the graph.

Design doc 9: local server, 127.0.0.1 only. This is still not Step 1-4's
full agent server (no tool node, no core MCP, no six scientific-artifact
tables) -- but unlike the first cut of this file, it is no longer a
throwaway shim: it goes through the real `graph.py` object with a real
checkpointer, on the same `~/.goldilocks/goldilocks.db` the six tables
will eventually share. Nothing here needs to be ripped out when the tool
node and interrupt edge land -- it only needs extending.

Contract: the client sends ``{thread_id, message}`` -- one new message,
never the full transcript. The checkpointer, keyed by `thread_id`, is the
only place conversation history lives (design doc 16: "状态的真相只能有
一处"). Resending full history here would fight the checkpointer's
message dedup and duplicate turns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import convert_to_openai_messages
from langgraph.types import Command
from pydantic import BaseModel

from goldilocks_agent import core_server, store
from goldilocks_agent.config import (
    configured_providers,
    read_experience_level,
    write_credential,
    write_experience_level,
)
from goldilocks_agent.graph import build_graph, open_checkpointer
from goldilocks_agent.tools import mlip_playground, structure_search
from goldilocks_agent.tools.structure_search import jarvis_cache

logger = logging.getLogger(__name__)

# asyncio only holds a *weak* reference to a task -- without keeping our own
# strong reference somewhere, the background download below could be
# garbage-collected mid-flight. This set exists purely to hold that
# reference; entries remove themselves once done.
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    jarvis_cache.load()
    if not jarvis_cache.is_available():
        # First run on this machine: fetch the ~200MB dataset once, in the
        # background, so the app is usable immediately (Materials Cloud/
        # NOMAD/MP don't need it) instead of requiring a user to know about
        # a CLI command. `download_cache()` sets the in-memory cache itself
        # on completion, so JARVIS results start working with no restart.
        logger.info("JARVIS cache not found -- downloading in the background")
        task = asyncio.create_task(jarvis_cache.download_cache())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    async with open_checkpointer() as checkpointer, store.open_store() as db:
        app.state.graph = build_graph(checkpointer)
        app.state.store = db
        yield
    # Belt and suspenders alongside `core_server`'s own `atexit` hook (see
    # its docstring) -- covers the graceful-shutdown path explicitly too.
    core_server.shutdown()


app = FastAPI(lifespan=lifespan)


class ChatMessage(BaseModel):
    role: str
    # Plain string for the common text-only turn; a list of OpenAI-style
    # content parts (`{"type": "text", ...}` / `{"type": "image_url", ...}`)
    # when an image is attached -- Qwen3.8 is vision-capable and litellm's
    # ollama_chat provider accepts this shape as-is (verified 2026-09-15).
    content: str | list[dict]


class ChatRequest(BaseModel):
    thread_id: str
    # Exactly one of `message`/`resume` is set. `message` is the normal
    # "user sent a new turn" case. `resume` answers a pending interrupt and
    # carries no new message of its own; the graph was already paused
    # mid-turn waiting for exactly this. Two shapes, depending on which SSE
    # event asked for it: `{"approved": true}` for a `confirmation_needed`
    # card (design doc 17.10), or `{"result": {...}}`/`{"error": "..."}`
    # for a `client_tool_call` (graph.py's CLIENT_EXECUTED_TOOLS) -- the
    # browser reporting back what it actually did.
    message: ChatMessage | None = None
    resume: dict | None = None
    model_id: str | None = None
    # Sidebar index fields -- written to the `conversations` table, never
    # replayed into the graph. `title` is the client's already-computed
    # (truncated) title; `project_id` only takes effect the first time this
    # thread_id is seen (see store.touch_conversation).
    title: str | None = None
    project_id: str | None = None
    # Accepted but not yet used -- no tool routing or system-prompt customization
    # exists until the tool node lands (Step 3 proper).
    tool: str | None = None
    workspace_state: dict | None = None


class CredentialRequest(BaseModel):
    provider: str
    api_key: str


class ProjectCreate(BaseModel):
    name: str
    color: str
    description: str = ""


class ConversationUpdate(BaseModel):
    project_id: str | None = None


class PreferencesUpdate(BaseModel):
    experience_level: str


class MlipSinglePointRequest(BaseModel):
    structure_content: str
    structure_name: str
    arch: str = "mace_mp"


class MlipGeomOptRequest(BaseModel):
    structure_content: str
    structure_name: str
    arch: str = "mace_mp"
    fmax: float = 0.1
    steps: int = 1000
    relax_mode: str = "ionic"


class MlipEosRequest(BaseModel):
    structure_content: str
    structure_name: str
    arch: str = "mace_mp"
    min_volume: float = 0.95
    max_volume: float = 1.05
    n_volumes: int = 7


class MlipNebRequest(BaseModel):
    init_structure_content: str
    init_structure_name: str
    final_structure_content: str
    final_structure_name: str
    arch: str = "mace_mp"
    n_images: int = 15
    fmax: float = 0.1


class MlipPhononsRequest(BaseModel):
    structure_content: str
    structure_name: str
    arch: str = "mace_mp"
    supercell: int = 2
    displacement: float = 0.01


class StructureMatchRequest(BaseModel):
    mode: str  # "file" | "formula"
    formula: str | None = None
    # Matches the search panel's property filter chips -- same enum
    # (`tools/structure_search/tool.py`'s TOOL_SCHEMA) the LLM tool uses,
    # so panel-direct and chat-driven searches apply the same filter.
    properties: list[str] | None = None
    # "file" mode fields, accepted but not yet actionable -- see the 501 below.
    structure_content: str | None = None
    structure_name: str | None = None


def _pending_interrupt_event(pending: dict) -> str:
    """Same paused-interrupt plumbing (design doc 17.10) serves two
    different purposes, distinguished by `graph.py`'s `call_tool`: a plain
    confirmation gate (show a card, wait for a human click) vs. a
    `CLIENT_EXECUTED_TOOLS` handoff (run this in the browser via
    `coreWorkspace.dispatch(...)`, report the real result back). The
    frontend needs a different `event:` name to tell which one it got."""
    if pending.get("client_execute"):
        return "client_tool_call"
    return "confirmation_needed"


async def _stream_reply(
    graph,
    thread_id: str,
    model_id: str | None,
    *,
    message: dict | None = None,
    resume: dict | None = None,
) -> AsyncIterator[str]:
    config = {
        "configurable": {
            "thread_id": thread_id,
            "model_id": model_id,
            # Read server-side, not client-supplied -- one source of truth
            # (design doc 16), and it means changing the setting in Settings
            # takes effect on the very next turn of every open chat.
            "experience_level": read_experience_level(),
        }
    }
    # A resume answers a pending interrupt (see ChatRequest) -- the graph is
    # already paused mid-turn, so there's no new message to feed in, only
    # the decision the paused `interrupt()` call is waiting on.
    graph_input = (
        Command(resume=resume) if resume is not None else {"messages": [message]}
    )
    try:
        async for chunk in graph.astream(
            graph_input, config=config, stream_mode="custom"
        ):
            # Plain text deltas (the common case) are un-typed strings from
            # `writer(delta.content)`; `call_llm`/`call_tool` also emit typed
            # dict chunks (`tool_status`/`tool_result`) for the frontend's
            # SSE `event:` handlers -- design doc 12.3's "one status line,
            # never the raw args/results, in chat" plus panel-sync both ride
            # this same distinction.
            if isinstance(chunk, dict) and "type" in chunk:
                yield f"event: {chunk['type']}\ndata: {json.dumps(chunk)}\n\n"
            else:
                yield f"data: {json.dumps(chunk)}\n\n"
        # `astream` above just ends, with no special chunk, the moment a node
        # calls `interrupt()` -- the pending confirmation only shows up in
        # the graph's checkpointed state, so it has to be fetched separately
        # rather than caught mid-stream (design doc 17.10's confirmation card).
        snapshot = await graph.aget_state(config)
        if snapshot.next:
            pending = snapshot.tasks[0].interrupts[0].value
            event = _pending_interrupt_event(pending)
            yield f"event: {event}\ndata: {json.dumps(pending)}\n\n"
    except openai.OpenAIError as exc:
        # litellm normalizes every provider's errors onto the openai-sdk hierarchy,
        # so this catches bad/missing API keys, unknown models, timeouts, etc.
        # uniformly, whichever provider the caller was routed to.
        yield f"data: {json.dumps(f'⚠️ {exc}')}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    if request.resume is not None:
        # Answering a pending confirmation card isn't a new conversation
        # turn from the sidebar's point of view -- nothing to (re)index.
        return StreamingResponse(
            _stream_reply(
                app.state.graph,
                request.thread_id,
                request.model_id,
                resume=request.resume,
            ),
            media_type="text/event-stream",
        )
    if request.message is None:
        raise HTTPException(status_code=400, detail="message or resume is required")
    # Indexed before the model call, not after -- a chat that errors mid-stream
    # still happened and still belongs in the sidebar (design doc 16: the index
    # tracks conversations, not successful completions).
    content = request.message.content
    fallback_title = content[:48] if isinstance(content, str) else "New chat"
    await store.touch_conversation(
        app.state.store,
        request.thread_id,
        title=request.title or fallback_title or "New chat",
        project_id=request.project_id,
    )
    return StreamingResponse(
        _stream_reply(
            app.state.graph,
            request.thread_id,
            request.model_id,
            message=request.message.model_dump(),
        ),
        media_type="text/event-stream",
    )


@app.get("/api/chat/{thread_id}")
async def get_chat_history(thread_id: str) -> dict:
    """Rehydrate a conversation's messages from the checkpointer.

    The sidebar's `conversations` row only has title/timestamps -- the
    actual transcript lives solely in the checkpointer (design doc 16:
    "状态的真相只能有一处"), so reopening a past chat means reading it
    from there, not from a second copy.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await app.state.graph.aget_state(config)
    messages = snapshot.values.get("messages", []) if snapshot.values else []
    return {"messages": convert_to_openai_messages(messages)}


@app.post("/api/credentials")
async def save_credential(request: CredentialRequest) -> dict:
    write_credential(request.provider, request.api_key)
    return {"ok": True}


@app.get("/api/credentials")
async def get_credentials_status() -> dict[str, bool]:
    """Presence per provider only -- never returns the key itself."""
    return configured_providers()


@app.get("/api/preferences")
async def get_preferences() -> dict:
    return {"experience_level": read_experience_level()}


@app.post("/api/preferences")
async def save_preferences(request: PreferencesUpdate) -> dict:
    try:
        write_experience_level(request.experience_level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/projects")
async def list_projects() -> list[dict]:
    return await store.list_projects(app.state.store)


@app.post("/api/projects")
async def create_project(request: ProjectCreate) -> dict:
    return await store.create_project(
        app.state.store, request.name, request.color, request.description
    )


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> dict:
    await store.delete_project(app.state.store, project_id)
    return {"ok": True}


@app.get("/api/conversations")
async def list_conversations() -> list[dict]:
    return await store.list_conversations(app.state.store)


@app.patch("/api/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str, request: ConversationUpdate
) -> dict:
    await store.set_conversation_project(
        app.state.store, conversation_id, request.project_id
    )
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    checkpointer = app.state.graph.checkpointer
    if checkpointer is None:
        raise HTTPException(status_code=500, detail="checkpointer not configured")
    await store.delete_conversation(app.state.store, conversation_id)
    await checkpointer.adelete_thread(conversation_id)
    return {"ok": True}


@app.post("/api/structure-match")
async def structure_match(
    request: StructureMatchRequest,
) -> structure_search.GroupedSearchResult:
    if request.mode == "file":
        # Not blocked on core (2026-09-15: corrected an earlier wrong note
        # here) -- the old webapp's file mode only used
        # `goldilocks_core.structure.{features,io}` for plain
        # pymatgen/spglib operations (Structure.from_file + SpacegroupAnalyzer
        # + Composition), the same things `query.py`'s formula mode already
        # does with zero core dependency. This is just not implemented yet,
        # a scoping choice, not something waiting on the core MCP client.
        raise HTTPException(
            status_code=501,
            detail="File-based structure matching isn't implemented yet.",
        )
    if request.mode != "formula":
        raise HTTPException(status_code=400, detail=f"Unknown mode: {request.mode!r}")
    if not request.formula:
        raise HTTPException(status_code=400, detail="formula required for formula mode")
    try:
        return await structure_search.find_in_databases(
            request.formula, request.properties
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fetch-structure")
async def fetch_structure(
    source: str, entry_id: str
) -> structure_search.FetchedStructure:
    try:
        return await structure_search.fetch_structure(source, entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Missing API key / JARVIS cache not downloaded -- misconfigured, not missing.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch from {source}: {exc}"
        ) from exc


def _mlip_response(result) -> dict:
    return {"raw": result.model_dump(), "summary": result.summary()}


async def _run_mlip(coro) -> dict:
    """Shared error translation for the 5 routes below -- no confirmation
    gate here (unlike the LLM tool-calling path in graph.py): clicking "Run
    calculation" in the panel *is* the user's explicit local action, same
    reasoning as `/api/structure-match` needing none."""
    try:
        result = await coro
    except RuntimeError as exc:
        # Not enabled (GOLDILOCKS_AGENT_MLIP_ENABLED unset) or the `janus`
        # CLI subprocess itself failed/timed out.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _mlip_response(result)


@app.post("/api/mlip/singlepoint")
async def mlip_singlepoint(request: MlipSinglePointRequest) -> dict:
    return await _run_mlip(
        mlip_playground.run_singlepoint(
            request.structure_content, request.structure_name, request.arch
        )
    )


@app.post("/api/mlip/geomopt")
async def mlip_geomopt(request: MlipGeomOptRequest) -> dict:
    return await _run_mlip(
        mlip_playground.run_geometry_optimization(
            request.structure_content,
            request.structure_name,
            request.arch,
            request.fmax,
            request.steps,
            request.relax_mode,
        )
    )


@app.post("/api/mlip/eos")
async def mlip_eos(request: MlipEosRequest) -> dict:
    return await _run_mlip(
        mlip_playground.run_equation_of_state(
            request.structure_content,
            request.structure_name,
            request.arch,
            request.min_volume,
            request.max_volume,
            request.n_volumes,
        )
    )


@app.post("/api/mlip/neb")
async def mlip_neb(request: MlipNebRequest) -> dict:
    return await _run_mlip(
        mlip_playground.run_neb(
            request.init_structure_content,
            request.init_structure_name,
            request.final_structure_content,
            request.final_structure_name,
            request.arch,
            request.n_images,
            request.fmax,
        )
    )


@app.post("/api/mlip/phonons")
async def mlip_phonons(request: MlipPhononsRequest) -> dict:
    return await _run_mlip(
        mlip_playground.run_phonons(
            request.structure_content,
            request.structure_name,
            request.arch,
            request.supercell,
            request.displacement,
        )
    )


@app.post("/api/core-server/ensure")
async def core_server_ensure() -> dict:
    """The frontend calls this the moment DFT Workbench opens (inline or
    full-page) -- lazily ensures goldilocks-core's own HTTP backend
    (`goldilocks serve http`, embedding-target for the `goldilocks-workbench`
    npm package) is running, local/desktop deployment only (see
    `core_server`'s module docstring). Non-blocking: returns immediately
    with the current status; the frontend polls this same route until it
    reports ``ready``/``error``/``not_configured`` rather than waiting on
    one long request."""
    return core_server.ensure_running()


_CORE_PROXY_HOP_BY_HOP_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "host",
}


async def _proxy_to_core(request: Request) -> Response:
    """Forwards one of `core_server.CORE_PROXIED_PATHS` to goldilocks-core's
    own HTTP backend (`core_server.ensure_running()`'s spawned process,
    same container in Docker, `127.0.0.1:8000`) -- see that module's own
    comment for why this exists: the embedded `goldilocks-workbench`
    frontend calls these paths relative to its own origin, which a dev
    server's proxy handles but a built/production deployment has no dev
    server to do for it. One handler registered once per path in
    `CORE_PROXIED_PATHS` below, not a catch-all -- this only ever forwards
    the exact known set, everything else still falls through to the SPA
    mount (registered after this, so it never shadows these).

    goldilocks-core isn't always there to answer yet: `ensure_running()` is
    lazy (only triggered by `/api/core-server/ensure`, called when the
    frontend opens the DFT Workbench Tool) and, with mMACE enabled, its
    first-ever cold start can take several minutes. The frontend's own
    polling already waits for `coreServerStatus.status == "ready"` before
    mounting anything that calls these paths, but that's a client-side
    convention, not something this route can rely on (a stale tab, a
    direct call, anything). Without a `try`/`except` here, a plain
    `httpx.ConnectError` during that window used to propagate unhandled
    and surface as a bare, uninformative 500.
    """
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            core_response = await client.request(
                request.method,
                f"{core_server.CORE_SERVER_BASE_URL}{request.url.path}",
                content=await request.body(),
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in _CORE_PROXY_HOP_BY_HOP_HEADERS
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "goldilocks-core isn't answering yet -- it may still be "
                "starting (mMACE's first-time setup can take several "
                "minutes). Wait for the Tool's own status message and "
                "retry."
            ),
        ) from exc
    return Response(
        content=core_response.content,
        status_code=core_response.status_code,
        headers={
            k: v
            for k, v in core_response.headers.items()
            if k.lower() not in _CORE_PROXY_HOP_BY_HOP_HEADERS
        },
        media_type=core_response.headers.get("content-type"),
    )


for _core_path in core_server.CORE_PROXIED_PATHS:
    app.add_api_route(
        _core_path,
        _proxy_to_core,
        methods=["GET", "POST"],
        name=f"proxy_to_core{_core_path.replace('/', '_').replace('-', '_')}",
    )


# Opt-in only (unset in normal dev, where the frontend is vite's own dev
# server) -- the Docker image sets this to the built `app/dist` it copies
# in, so the same FastAPI process can serve the SPA alongside `/api/*`.
# Mounted last so it doesn't shadow any route registered above.
_STATIC_DIR = os.environ.get("GOLDILOCKS_AGENT_STATIC_DIR")
if _STATIC_DIR and Path(_STATIC_DIR).is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
