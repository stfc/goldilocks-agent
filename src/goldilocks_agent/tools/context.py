"""What a tool that works on the chat itself is given (#142).

Most tools only need their arguments. The DFT Workbench tools change the
chat's own draft on the server, so they also get whose chat it is, what
it holds, a way to tell the browser what changed, and the services the
agent server lends a graph run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import aiosqlite
from langchain_core.messages import BaseMessage

from goldilocks_agent.core_api import CoreApi


@dataclass(frozen=True)
class AgentServices:
    """The services `server.py` lends each graph run, as LangGraph's run
    context: the chat database and the goldilocks-core client."""

    store: aiosqlite.Connection
    core: CoreApi


@dataclass(frozen=True)
class ToolContext:
    """One call's view of its chat."""

    thread_id: str
    # The thread's checkpointed messages, the turn's tool calls included.
    messages: Sequence[BaseMessage]
    # Sends a typed event to the browser over the chat's SSE stream.
    emit: Callable[[dict], None]
    store: aiosqlite.Connection
    core: CoreApi
