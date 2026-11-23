"""Queryable index for projects and conversations.

Design doc 16.5: the checkpointer owns message content and graph state --
its storage format isn't queryable, so the sidebar (list, group by project,
search) needs its own tables keyed by the same `thread_id`. This module
owns two of the six: `projects` and `conversations`. It also keeps each chat's
DFT Workbench draft (`dft_drafts`, see `dft_draft.py`), which the panel
rewrites on every settings change -- too often for graph state. The other four
(`structures`, `bundles`, `values`, `llm_calls`) land once a tool actually
produces something to record -- no point creating empty tables early.

Same db file as the checkpointer (`~/.goldilocks/goldilocks.db`), never a
second file -- opened as its own connection since aiosqlite connections
aren't shareable across async contexts, but SQLite's WAL mode (already
enabled by the checkpointer's own setup) makes two connections to one file
safe for this traffic pattern.

`projects.description` is a 2026-09-15 addition beyond the design doc's
original `id · name · color · created_at` -- the project-card UI already
displayed a description, so the column just makes that persistent. Same
kind of dated, additive amendment as `bundles.sink/aiida_pk/job_status`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

# design doc 16.5: one .db, shared by the checkpointer (graph.py) and these
# tables -- never a second file, never split from the checkpointer.
DB_PATH = Path.home() / ".goldilocks" / "goldilocks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    working_dir TEXT,
    mode TEXT,
    updated_at TEXT NOT NULL,
    last_viewed_at TEXT
);

-- Shared deployment only: when each thread last had a chat request, so
-- chat_retention.py can delete the ones left idle (design doc §19.2).
CREATE TABLE IF NOT EXISTS chat_activity (
    thread_id TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL
);

-- One DFT Workbench draft per chat (dft_draft.py, #142), as JSON. Its own
-- table rather than graph state: the panel saves it after every settings
-- change, and a graph-state write copies the chat's whole history.
CREATE TABLE IF NOT EXISTS dft_drafts (
    thread_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    draft TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@asynccontextmanager
async def open_store() -> AsyncIterator[aiosqlite.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(_SCHEMA)
        await db.commit()
        db.row_factory = aiosqlite.Row
        yield db


async def list_projects(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute("SELECT * FROM projects ORDER BY created_at DESC")
    return [dict(row) for row in await cursor.fetchall()]


async def create_project(
    db: aiosqlite.Connection, name: str, color: str, description: str = ""
) -> dict:
    project = {
        "id": str(uuid.uuid4()),
        "name": name,
        "color": color,
        "description": description,
        "created_at": _now(),
    }
    await db.execute(
        "INSERT INTO projects (id, name, color, description, created_at) "
        "VALUES (:id, :name, :color, :description, :created_at)",
        project,
    )
    await db.commit()
    return project


async def delete_project(db: aiosqlite.Connection, project_id: str) -> None:
    await db.execute(
        "UPDATE conversations SET project_id = NULL WHERE project_id = ?", (project_id,)
    )
    await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await db.commit()


async def list_conversations(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute("SELECT * FROM conversations ORDER BY updated_at DESC")
    return [dict(row) for row in await cursor.fetchall()]


async def touch_conversation(
    db: aiosqlite.Connection,
    conversation_id: str,
    title: str,
    project_id: str | None = None,
) -> None:
    """Create-or-update a conversation's index row.

    Called on every chat turn -- the checkpointer already dedups message
    content by thread_id, this just keeps the sidebar's queryable copy of
    title/updated_at in sync. `project_id` is only applied on first
    creation; changing a conversation's project later goes through
    `set_conversation_project` (the "Add to Project" action), not here --
    otherwise a stale client-side `project_id` on a later turn could
    silently move a chat back out of its project.
    """
    now = _now()
    cursor = await db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
    )
    exists = await cursor.fetchone()
    if exists is None:
        await db.execute(
            "INSERT INTO conversations "
            "(id, project_id, title, working_dir, mode, updated_at, last_viewed_at) "
            "VALUES (?, ?, ?, NULL, NULL, ?, ?)",
            (conversation_id, project_id, title, now, now),
        )
    else:
        await db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conversation_id),
        )
    await db.commit()


async def set_conversation_project(
    db: aiosqlite.Connection, conversation_id: str, project_id: str | None
) -> None:
    await db.execute(
        "UPDATE conversations SET project_id = ? WHERE id = ?",
        (project_id, conversation_id),
    )
    await db.commit()


async def delete_conversation(db: aiosqlite.Connection, conversation_id: str) -> None:
    await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    await db.commit()


async def touch_chat_activity(db: aiosqlite.Connection, thread_id: str) -> None:
    """Marks `thread_id` as used now."""
    await db.execute(
        "INSERT INTO chat_activity (thread_id, last_seen) VALUES (?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET last_seen = excluded.last_seen",
        (thread_id, _now()),
    )
    await db.commit()


async def record_chat_activity(
    db: aiosqlite.Connection, last_seen: dict[str, str]
) -> None:
    """Adds a row per thread in `last_seen` (thread id -> ISO time) that has
    none yet; threads already recorded keep theirs."""
    await db.executemany(
        "INSERT OR IGNORE INTO chat_activity (thread_id, last_seen) VALUES (?, ?)",
        list(last_seen.items()),
    )
    await db.commit()


async def chat_last_seen(db: aiosqlite.Connection) -> dict[str, datetime]:
    """Every recorded thread's last use."""
    cursor = await db.execute("SELECT thread_id, last_seen FROM chat_activity")
    rows = await cursor.fetchall()
    return {row[0]: datetime.fromisoformat(row[1]) for row in rows}


async def forget_chat_activity(db: aiosqlite.Connection, thread_id: str) -> None:
    await db.execute("DELETE FROM chat_activity WHERE thread_id = ?", (thread_id,))
    await db.commit()


async def get_dft_draft(db: aiosqlite.Connection, thread_id: str) -> dict | None:
    """`thread_id`'s DFT Workbench draft with its `revision`, or None."""
    cursor = await db.execute(
        "SELECT revision, draft FROM dft_drafts WHERE thread_id = ?", (thread_id,)
    )
    row = await cursor.fetchone()
    return None if row is None else {"revision": row[0], **json.loads(row[1])}


async def put_dft_draft(
    db: aiosqlite.Connection, thread_id: str, draft: dict, base_revision: int
) -> tuple[bool, dict | None]:
    """Replaces `thread_id`'s draft if its revision is still `base_revision`
    (0: there is none yet), as revision `base_revision + 1`. Returns whether
    it did, and the draft now stored. One conditional statement, so two
    writers from the same revision can't both succeed."""
    if base_revision == 0:
        cursor = await db.execute(
            "INSERT INTO dft_drafts (thread_id, revision, draft, updated_at) "
            "VALUES (?, 1, ?, ?) ON CONFLICT(thread_id) DO NOTHING",
            (thread_id, json.dumps(draft), _now()),
        )
    else:
        cursor = await db.execute(
            "UPDATE dft_drafts SET revision = revision + 1, draft = ?, "
            "updated_at = ? WHERE thread_id = ? AND revision = ?",
            (json.dumps(draft), _now(), thread_id, base_revision),
        )
    await db.commit()
    return cursor.rowcount == 1, await get_dft_draft(db, thread_id)


async def delete_dft_draft(db: aiosqlite.Connection, thread_id: str) -> None:
    await db.execute("DELETE FROM dft_drafts WHERE thread_id = ?", (thread_id,))
    await db.commit()
