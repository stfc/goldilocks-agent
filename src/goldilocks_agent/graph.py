"""LangGraph graph: ``llm`` node, ``tool`` node, plus an interrupt inside
``tool`` for confirmation-gated calls (design doc 17.3/17.4/17.10).

Step 3 in docs/goldilocks-agent-implementation-plan.md called for an
``llm`` node, a ``tool`` node, and an ``interrupt`` edge. MLIP Playground
(2026-09-15) is the first Tool that needs one -- every tool name in
`goldilocks_agent.tools.CONFIRMATION_REQUIRED_TOOLS` pauses via
`langgraph.types.interrupt()` inside `call_tool` before it runs, every
single call (the user's explicit choice: consent is never remembered
across calls in the same chat, unlike e.g. a one-time file-import
authorization). No dedicated graph node/edge was needed for this --
`interrupt()` just pauses mid-node, so the graph shape (2 nodes, one
conditional edge) is unchanged.

Because LangGraph replays a node's whole function body from the top on
each resume, a turn with *two* gated calls pauses twice in sequence: the
first `Command(resume=...)` unblocks call 1's interrupt (which then runs
for real, for the first time) and execution immediately reaches call 2's
still-fresh interrupt, pausing again. This is expected, not a bug --
`server.py`/the frontend must be able to handle a sequence of
confirmation cards for one turn, not assume exactly one.

Conversation engine, not a contract model: no ``target_contract``, never
refuses to answer (see design doc 11.3).

Checkpointer is not optional scaffolding: design doc 16 ("状态的真相只能
有一处") makes the checkpointer the *only* place conversation/graph state
lives -- callers pass a ``thread_id`` and only the new message, never the
full history, or they'll fight the checkpointer's own message dedup.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import litellm
from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from goldilocks_agent.config import get_api_key
from goldilocks_agent.tools import (
    CONFIRMATION_LABELS,
    CONFIRMATION_REQUIRED_TOOLS,
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
)

# design doc 16.5: one .db, shared with the six scientific-artifact tables --
# never a second file, never split from the checkpointer.
DB_PATH = Path.home() / ".goldilocks" / "goldilocks.db"

# Registered under this exact name by `ollama pull qwen3.8` -- Ollama's own
# official library entry (ollama.com/library/qwen3.8), not a third-party
# HuggingFace GGUF passthrough (2026-09-15: found and corrected after
# initially missing that Ollama carries this model under its own top-level
# name rather than as a size tag under `qwen3`). Not our fine-tune, so no
# PSDI verification chain (design doc 11.4, 2026-09-15 amendment) --
# model_identity_pinned must be recorded false wherever this gets logged.
LOCAL_MODEL = "ollama_chat/qwen3.8:latest"

# Frontend model-selector id -> (credential provider, litellm model string).
# `app/src/App.jsx`'s `MODEL_GROUPS` is the source of these ids.
# ⚠️ "anthropic" and "google" have been verified against a real API call
# (2026-09-15, user's own key -- google's first string, `gemini-2.0-flash`,
# was rejected by Google's own API with a 404 naming `gemini-3.6-flash` as
# the replacement, which is what's recorded below). "openai" is still an
# unverified best-effort guess -- expect to correct it the first time
# someone actually tests an OpenAI key.
CLOUD_MODELS = {
    "anthropic-claude": ("anthropic", "anthropic/claude-sonnet-5"),
    "openai-gpt": ("openai", "openai/gpt-4o"),
    "google-gemini": ("google", "gemini/gemini-3.6-flash"),
}


def resolve_model(model_id: str | None = None) -> tuple[str, dict]:
    """Pick the conversation engine model and any extra litellm kwargs (e.g. api_key).

    `GOLDILOCKS_AGENT_MODEL` env var wins over everything (dev/test escape hatch).
    Otherwise: a recognized cloud `model_id` routes to that provider using its
    stored credential (design doc 11.2); anything else falls back to local Ollama.
    """
    override = os.environ.get("GOLDILOCKS_AGENT_MODEL")
    if override:
        return override, {}
    if model_id in CLOUD_MODELS:
        provider, litellm_model = CLOUD_MODELS[model_id]
        api_key = get_api_key(provider)
        return litellm_model, ({"api_key": api_key} if api_key else {})
    return LOCAL_MODEL, {}


# Modeled on ChatGPT/Claude's "custom instructions" -- a short, fixed blurb
# about the user, injected as a system message rather than stored as a
# conversation turn. Design doc 12.2 (★ already decided, never actually
# wired up until now): this must only change explanation depth/register,
# **never** which values or parameters get recommended -- the guardrail
# sentence below says so explicitly so the model doesn't conflate "less
# experienced" with "give safer/different numbers."
_EXPERIENCE_LEVEL_PROMPTS = {
    "new": (
        "The user is new to computational materials science. Explain "
        "concepts and terminology in more depth, define jargon, and give "
        "more context and guidance than you would for an expert."
    ),
    "familiar": (
        "The user is familiar with computational materials workflows and "
        "knows the basics. Give practical, workflow-oriented help without "
        "over-explaining fundamentals."
    ),
    "advanced": (
        "The user is an advanced computational materials researcher. Be "
        "concise and expert-oriented -- skip basic explanations unless "
        "asked, and use technical terminology freely."
    ),
}

_EXPERIENCE_LEVEL_GUARDRAIL = (
    "This only affects how much you explain and which register you use -- "
    "it must never change which values, defaults, or parameters you would "
    "otherwise recommend for a calculation."
)


def experience_level_system_message(level: str | None) -> dict | None:
    prompt = _EXPERIENCE_LEVEL_PROMPTS.get(level or "")
    if prompt is None:
        return None
    return {"role": "system", "content": f"{prompt} {_EXPERIENCE_LEVEL_GUARDRAIL}"}


def _repair_orphaned_tool_calls(messages: list[dict]) -> list[dict]:
    """Patch over a real gap in `call_tool`'s confirmation interrupt (2026-09-16
    incident): an assistant message with `tool_calls` only reaches the
    checkpointer once `call_llm` returns, but the matching `tool` messages
    only land once `call_tool` finishes its whole loop and returns too --
    and a still-pending `interrupt()` (an unanswered confirmation card, or a
    second gated call in the same turn per this module's own docstring)
    means that never happens. If a plain new message arrives on that thread
    before it does -- the user types ahead instead of answering the card --
    LangGraph's own resume rule (`self.input is None or Command`, see
    `pregel/_loop.py::_first`) treats a plain dict input as a fresh run and
    discards the still-paused task, leaving those tool_calls permanently
    orphaned in `state["messages"]`. Every provider rejects that outright
    (`tool_use ids were found without tool_result blocks immediately
    after`), and since it's in the checkpointer it fails identically
    forever after. Repaired here, on the ephemeral list built for litellm
    each call, rather than written back to state -- cheap to redo every
    call, and it self-heals threads that are *already* corrupted in an
    existing checkpoint with no DB migration needed.
    """
    repaired: list[dict] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        repaired.append(message)
        i += 1
        tool_calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not tool_calls:
            continue
        expected_ids = [call["id"] for call in tool_calls]
        found_ids = set()
        while i < len(messages) and messages[i].get("role") == "tool":
            repaired.append(messages[i])
            found_ids.add(messages[i]["tool_call_id"])
            i += 1
        for call_id in expected_ids:
            if call_id in found_ids:
                continue
            repaired.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "error": (
                                "This tool call was interrupted before it "
                                "completed (e.g. a pending confirmation was "
                                "never answered) and its result was lost."
                            )
                        }
                    ),
                }
            )
    return repaired


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(DB_PATH)) as saver:
        await saver.setup()
        yield saver


async def call_llm(state: MessagesState, config: RunnableConfig) -> dict:
    # litellm wants plain OpenAI-style dicts, not LangChain message objects --
    # passing BaseMessage instances through silently mangles the role (observed:
    # a HumanMessage arrived at Ollama tagged role="assistant").
    messages = _repair_orphaned_tool_calls(
        convert_to_openai_messages(state["messages"])
    )
    configurable = config.get("configurable", {})
    experience_level = configurable.get("experience_level")
    system_message = experience_level_system_message(experience_level)
    if system_message:
        # Injected fresh every call, never written into `state["messages"]` --
        # it's a live preference (design doc 12.2), not conversation content
        # or provenance, so it doesn't belong in the checkpointer's history
        # and can change immediately if the user changes the setting mid-chat.
        messages = [system_message, *messages]
    model_id = configurable.get("model_id")
    model, extra = resolve_model(model_id)
    writer = get_stream_writer()
    content = ""
    # Keyed by the provider's own per-call-in-progress index -- a streamed
    # tool call's `arguments` arrives split across many chunks and must be
    # concatenated in order before it's valid JSON.
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    response = await litellm.acompletion(
        model=model, messages=messages, tools=TOOL_SCHEMAS, stream=True, **extra
    )
    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content:
            content += delta.content
            writer(delta.content)
        for tool_call in delta.tool_calls or []:
            slot = tool_calls_acc.setdefault(
                tool_call.index, {"id": None, "name": None, "arguments": ""}
            )
            if tool_call.id:
                slot["id"] = tool_call.id
            if tool_call.function and tool_call.function.name:
                slot["name"] = tool_call.function.name
            if tool_call.function and tool_call.function.arguments:
                slot["arguments"] += tool_call.function.arguments

    if not tool_calls_acc:
        return {"messages": [{"role": "assistant", "content": content}]}

    tool_calls = [
        {
            "id": slot["id"],
            "type": "function",
            "function": {"name": slot["name"], "arguments": slot["arguments"]},
        }
        for slot in tool_calls_acc.values()
    ]
    # design doc 12.3: chat only narrates that a tool is running, one line,
    # never the raw args/results -- same shape as the old webapp's
    # `tool_status` SSE event ("Calculating k-points..."). A typed dict, not
    # plain text -- `server.py` turns this into a real SSE `event:
    # tool_status` frame for the frontend's existing (until now unwired)
    # status-line handler, and it never lands in `content` below, so it
    # never enters the checkpointer's persisted history either.
    for call in tool_calls:
        name = call["function"]["name"]
        # `tool` (the raw name) is for the frontend to map to a UI Tool id --
        # `label` is just for display, parsing it back out would be fragile.
        writer({"type": "tool_status", "tool": name, "label": f"Calling {name}..."})
    return {
        "messages": [
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        ]
    }


async def call_tool(state: MessagesState) -> dict:
    """Dispatch every tool call on the last (assistant) message.

    `state["messages"][-1].tool_calls` is LangChain's own normalized shape
    (`{"name", "args", "id"}, args already JSON-decoded`) -- `add_messages`
    did that conversion when `call_llm`'s raw OpenAI-shaped dict landed in
    state, see `_create_message_from_message_type` in langchain_core.
    """
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        # route_after_llm only sends control here when the last message has
        # tool_calls -- only AIMessage carries that attribute, so this would
        # mean the routing invariant itself broke, not a normal runtime case.
        raise TypeError(f"tool node reached with a non-AIMessage: {type(last)}")
    writer = get_stream_writer()
    results = []
    for call in last.tool_calls:
        name = call["name"]
        if name in CONFIRMATION_REQUIRED_TOOLS:
            decision = interrupt(
                {
                    "tool": name,
                    "args": call["args"],
                    "label": CONFIRMATION_LABELS[name](call["args"]),
                }
            )
            if not decision.get("approved"):
                output: Any = {"error": "User declined to run this calculation."}
                writer({"type": "tool_result", "tool": name, "result": output})
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(output),
                    }
                )
                continue
        fn = TOOL_DISPATCH.get(name)
        if fn is None:
            output = {"error": f"Unknown tool: {name!r}"}
        else:
            try:
                output = await fn(**call["args"])
            except Exception as exc:  # noqa: BLE001
                # A failed lookup (bad formula, unknown source/entry_id, a
                # source's API erroring) is data the LLM should narrate to
                # the user, not a crashed graph run.
                output = {"error": str(exc)}
        # Panel gets the full result (design doc 12.3: the moment a tool
        # call lands, the right-side panel's matching fields update too,
        # over its own `event: tool_result` SSE frame) -- but the LLM's own
        # copy prefers `model_dump_for_llm()` when the tool defines one
        # (heavy visual/array fields like SVG plots trimmed out, since this
        # json.dumps() lands in the checkpointer's history forever, unlike
        # the panel's one-shot render).
        panel_output = output.model_dump() if hasattr(output, "model_dump") else output
        if hasattr(output, "model_dump_for_llm"):
            llm_output = output.model_dump_for_llm()
        else:
            llm_output = panel_output
        writer({"type": "tool_result", "tool": name, "result": panel_output})
        results.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(llm_output),
            }
        )
    return {"messages": results}


def route_after_llm(state: MessagesState) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tool"
    return END


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("llm", call_llm)
    graph.add_node("tool", call_tool)
    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", route_after_llm, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")
    return graph.compile(checkpointer=checkpointer)
