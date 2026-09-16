"""Projects/conversations index (design doc 16.5: two of the six tables).

Structural checks only -- these tables exist so the sidebar can query
metadata the checkpointer's format can't answer (list, group by project,
search), so what matters is that the rows round-trip correctly, not any
particular LLM behavior.
"""

from __future__ import annotations

import asyncio

from goldilocks_agent import store


def _run(coro):
    return asyncio.run(coro)


def test_create_and_list_projects(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "goldilocks.db")

    async def scenario():
        async with store.open_store() as db:
            await store.create_project(
                db, "Perovskites", "#3b82f6", "Halide perovskite screen"
            )
            return await store.list_projects(db)

    projects = _run(scenario())
    assert len(projects) == 1
    assert projects[0]["name"] == "Perovskites"
    assert projects[0]["description"] == "Halide perovskite screen"


def test_touch_conversation_creates_then_updates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "goldilocks.db")

    async def scenario():
        async with store.open_store() as db:
            project = await store.create_project(db, "Perovskites", "#3b82f6")
            await store.touch_conversation(
                db, "thread-1", "First message", project["id"]
            )
            await store.touch_conversation(db, "thread-1", "First message")
            return await store.list_conversations(db), project["id"]

    conversations, project_id = _run(scenario())
    # Second touch_conversation call omitted project_id -- confirms it only
    # applies on insert and a later call can't accidentally clear it.
    assert len(conversations) == 1
    assert conversations[0]["project_id"] == project_id
    assert conversations[0]["title"] == "First message"


def test_delete_project_unassigns_its_conversations(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "goldilocks.db")

    async def scenario():
        async with store.open_store() as db:
            project = await store.create_project(db, "Perovskites", "#3b82f6")
            await store.touch_conversation(db, "thread-1", "hi", project["id"])
            await store.delete_project(db, project["id"])
            return await store.list_projects(db), await store.list_conversations(db)

    projects, conversations = _run(scenario())
    assert projects == []
    assert len(conversations) == 1
    assert conversations[0]["project_id"] is None


def test_set_conversation_project_and_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "goldilocks.db")

    async def scenario():
        async with store.open_store() as db:
            await store.touch_conversation(db, "thread-1", "hi")
            project = await store.create_project(db, "Perovskites", "#3b82f6")
            await store.set_conversation_project(db, "thread-1", project["id"])
            after_set = await store.list_conversations(db)
            await store.delete_conversation(db, "thread-1")
            after_delete = await store.list_conversations(db)
            return after_set, after_delete

    after_set, after_delete = _run(scenario())
    assert after_set[0]["project_id"] is not None
    assert after_delete == []
