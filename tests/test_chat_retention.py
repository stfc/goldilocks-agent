"""The shared deployment deletes chats left idle (chat_retention.py); the
local build keeps them."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.base import empty_checkpoint

from goldilocks_agent import chat_retention, store
from goldilocks_agent.graph import open_checkpointer

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
DAY = timedelta(hours=24)


def _use_db(tmp_path, monkeypatch):
    db_path = tmp_path / "goldilocks.db"
    monkeypatch.setattr("goldilocks_agent.graph.DB_PATH", db_path)
    monkeypatch.setattr("goldilocks_agent.store.DB_PATH", db_path)
    return db_path


async def _checkpoint(saver, thread_id: str, at: datetime) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["ts"] = at.isoformat()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    await saver.aput(config, checkpoint, {}, {})


async def _threads(saver) -> set[str]:
    return {c.config["configurable"]["thread_id"] async for c in saver.alist(None)}


def test_existing_chats_are_dated_by_their_newest_checkpoint(tmp_path, monkeypatch):
    """Chats from before activity was recorded: the idle ones go at the
    first sweep, the recent ones stay."""
    _use_db(tmp_path, monkeypatch)

    async def scenario():
        async with open_checkpointer() as saver, store.open_store() as db:
            await _checkpoint(saver, "old", NOW - 3 * DAY)
            await _checkpoint(saver, "recent", NOW - 3 * DAY)
            await _checkpoint(saver, "recent", NOW - timedelta(hours=2))
            await chat_retention.record_existing_threads(saver, db)
            deleted = await chat_retention.delete_idle_threads(saver, db, DAY, NOW)
            return deleted, await _threads(saver), await store.chat_last_seen(db)

    deleted, threads, last_seen = asyncio.run(scenario())
    assert deleted == ["old"]
    assert threads == {"recent"}
    assert set(last_seen) == {"recent"}


def test_a_used_chat_is_kept_however_old_its_start(tmp_path, monkeypatch):
    """Activity, not the first checkpoint, decides; recording the existing
    threads again doesn't overwrite it."""
    _use_db(tmp_path, monkeypatch)

    async def scenario():
        async with open_checkpointer() as saver, store.open_store() as db:
            await _checkpoint(saver, "long-running", NOW - 3 * DAY)
            await store.touch_chat_activity(db, "long-running")
            await chat_retention.record_existing_threads(saver, db)
            deleted = await chat_retention.delete_idle_threads(
                saver, db, DAY, datetime.now(UTC)
            )
            return deleted, await _threads(saver)

    deleted, threads = asyncio.run(scenario())
    assert deleted == []
    assert threads == {"long-running"}


def _chat(client, thread_id: str) -> None:
    response = client.post(
        "/api/chat",
        json={"thread_id": thread_id, "message": {"role": "user", "content": "hi"}},
    )
    assert response.status_code == 200


def _activity(db_path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT thread_id FROM chat_activity")]


def test_shared_deployment_records_each_chat(tmp_path, monkeypatch):
    db_path = _use_db(tmp_path, monkeypatch)
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    monkeypatch.setenv("GOLDILOCKS_AGENT_MODEL", "not-a-real-provider/nope")
    from fastapi.testclient import TestClient

    from goldilocks_agent.server import app

    with TestClient(app) as client:
        _chat(client, "visitor-tab")
    assert _activity(db_path) == ["visitor-tab"]


def test_local_build_records_no_chat_activity(tmp_path, monkeypatch):
    db_path = _use_db(tmp_path, monkeypatch)
    monkeypatch.delenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", raising=False)
    monkeypatch.setenv("GOLDILOCKS_AGENT_MODEL", "not-a-real-provider/nope")
    from fastapi.testclient import TestClient

    from goldilocks_agent.server import app

    with TestClient(app) as client:
        _chat(client, "my-chat")
    assert _activity(db_path) == []
