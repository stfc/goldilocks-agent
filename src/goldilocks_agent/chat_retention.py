"""Deletes the shared deployment's idle chats (design doc §19.2).

There, the checkpointer holds every visitor's whole conversation, attached
structure files included: the server keeps the history so each request
carries only the new message, and a reloaded tab reads its chat back by
thread id. Closing the tab forgets the id but not the chat, so a thread with
no chat request for longer than the retention period
(`config.read_chat_retention()`) is deleted, checkpoints and all.

The local build never runs this: its chats are its user's own, on their own
machine, listed in the sidebar until they delete them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver

from goldilocks_agent import store

logger = logging.getLogger(__name__)

# How often the shared deployment looks for idle chats.
SWEEP_INTERVAL = timedelta(hours=1)


async def record_existing_threads(
    checkpointer: BaseCheckpointSaver, db: aiosqlite.Connection
) -> None:
    """Gives each checkpointed thread with no activity row one, dated by its
    newest checkpoint: chats from before activity was recorded."""
    newest: dict[str, str] = {}
    # Newest checkpoint first, so a thread's first one here is its latest.
    async for checkpoint in checkpointer.alist(None):
        thread_id = checkpoint.config["configurable"]["thread_id"]
        newest.setdefault(thread_id, checkpoint.checkpoint["ts"])
    await store.record_chat_activity(db, newest)


async def delete_idle_threads(
    checkpointer: BaseCheckpointSaver,
    db: aiosqlite.Connection,
    retention: timedelta,
    now: datetime | None = None,
) -> list[str]:
    """Deletes every thread last used more than `retention` before `now`;
    returns their ids."""
    cutoff = (now or datetime.now(UTC)) - retention
    idle = [
        thread_id
        for thread_id, last_seen in (await store.chat_last_seen(db)).items()
        if last_seen < cutoff
    ]
    for thread_id in idle:
        await checkpointer.adelete_thread(thread_id)
        await store.forget_chat_activity(db, thread_id)
    return idle


async def run(
    checkpointer: BaseCheckpointSaver, db: aiosqlite.Connection, retention: timedelta
) -> None:
    """Records the threads already there, then deletes the idle ones now
    and every `SWEEP_INTERVAL`, until cancelled."""
    try:
        await record_existing_threads(checkpointer, db)
    except Exception:
        logger.exception("could not date the existing chats")
    while True:
        try:
            deleted = await delete_idle_threads(checkpointer, db, retention)
            if deleted:
                logger.info(
                    "deleted %d chats idle for over %s", len(deleted), retention
                )
        except Exception:
            logger.exception("deleting idle chats failed")
        await asyncio.sleep(SWEEP_INTERVAL.total_seconds())
