"""A chat's DFT Workbench draft lives on the agent server (dft_draft.py,
#142): one per chat, written only from the revision it replaces, deleted
with the chat."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from goldilocks_agent import chat_retention, store
from goldilocks_agent.dft_draft import (
    DftDraft,
    StructureInput,
    compute_request,
    default_draft,
    open_structure,
    structure_key,
    structure_request,
)
from goldilocks_agent.graph import open_checkpointer

SI = {
    "structure_content": "data_Si\n_cell_length_a 5.431\n",
    "structure_name": "Si.cif",
    "structure_format": "cif",
}
DRAFT = {
    "vocabulary": "5",
    "structure": SI,
    "code": "quantum_espresso",
    "task": "scf_single_point",
    "hpc": None,
    "overrides": {"walltime_h": 24},
}


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr("goldilocks_agent.graph.DB_PATH", tmp_path / "goldilocks.db")
    monkeypatch.setattr("goldilocks_agent.store.DB_PATH", tmp_path / "goldilocks.db")
    from goldilocks_agent.server import app

    return TestClient(app)


def _put(client, thread_id: str, base_revision: int, **changes):
    return client.put(
        f"/api/chat/{thread_id}/dft/draft",
        json={"base_revision": base_revision, "draft": {**DRAFT, **changes}},
    )


def test_a_chat_has_no_draft_until_one_is_saved(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/chat/t1/dft/draft").json() == {"draft": None}

        saved = _put(client, "t1", 0)

        assert saved.status_code == 200
        assert saved.json() == {"draft": {"revision": 1, **DRAFT}}
        assert client.get("/api/chat/t1/dft/draft").json() == saved.json()


def test_each_chat_keeps_its_own_draft(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        _put(client, "t1", 0)
        _put(client, "t2", 0, task="phonon")

        assert client.get("/api/chat/t1/dft/draft").json()["draft"]["task"] == (
            "scf_single_point"
        )
        assert client.get("/api/chat/t2/dft/draft").json()["draft"]["task"] == "phonon"


def test_a_writer_from_an_older_revision_gets_the_newer_draft_back(
    tmp_path, monkeypatch
) -> None:
    """Design doc 3.3: a stale writer never overwrites newer state; it gets
    what is there to replay instead."""
    with _client(tmp_path, monkeypatch) as client:
        _put(client, "t1", 0)
        newer = _put(client, "t1", 1, task="band").json()["draft"]

        stale = _put(client, "t1", 1, task="phonon")
        again = _put(client, "t1", 0, task="relax")

        assert newer["revision"] == 2
        assert stale.status_code == 409
        assert stale.json()["draft"] == newer
        assert again.status_code == 409
        assert client.get("/api/chat/t1/dft/draft").json()["draft"] == newer


def test_a_draft_must_have_the_panels_shape(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        unknown_field = _put(client, "t1", 0, fetchMissing=True)
        no_task = client.put(
            "/api/chat/t1/dft/draft",
            json={"base_revision": 0, "draft": {"code": "quantum_espresso"}},
        )
        too_big = _put(
            client,
            "t1",
            0,
            structure={**SI, "structure_content": "x" * (5 * 1024 * 1024 + 1)},
        )

        assert unknown_field.status_code == 422
        assert no_task.status_code == 422
        assert too_big.status_code == 422
        assert client.get("/api/chat/t1/dft/draft").json() == {"draft": None}


def test_a_structure_less_draft_is_kept(tmp_path, monkeypatch) -> None:
    """The chat can set the task before any structure is open."""
    with _client(tmp_path, monkeypatch) as client:
        saved = _put(client, "t1", 0, structure=None, task="band")

        assert saved.json()["draft"]["structure"] is None
        assert saved.json()["draft"]["task"] == "band"


def test_deleting_a_local_chat_deletes_its_draft(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", raising=False)
    with _client(tmp_path, monkeypatch) as client:
        _put(client, "t1", 0)

        assert client.delete("/api/conversations/t1").status_code == 200

        assert client.get("/api/chat/t1/dft/draft").json() == {"draft": None}


def test_on_the_website_saving_a_draft_keeps_the_chat_alive(
    tmp_path, monkeypatch
) -> None:
    """A tab using only the panel is still using the chat: retention must
    not delete it as idle."""
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    with _client(tmp_path, monkeypatch) as client:
        _put(client, "t1", 0)

        async def last_seen():
            async with store.open_store() as db:
                return await store.chat_last_seen(db)

        assert "t1" in asyncio.run(last_seen())


def test_retention_deletes_an_idle_chats_draft(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "goldilocks.db"
    monkeypatch.setattr("goldilocks_agent.graph.DB_PATH", db_path)
    monkeypatch.setattr("goldilocks_agent.store.DB_PATH", db_path)
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)

    async def scenario():
        async with open_checkpointer() as saver, store.open_store() as db:
            for thread_id in ("idle", "used"):
                await store.put_dft_draft(db, thread_id, DRAFT, 0)
            await store.record_chat_activity(
                db,
                {
                    "idle": (now - timedelta(days=3)).isoformat(),
                    "used": (now - timedelta(hours=1)).isoformat(),
                },
            )
            await chat_retention.delete_idle_threads(saver, db, timedelta(days=1), now)
            return [
                await store.get_dft_draft(db, thread_id)
                for thread_id in ("idle", "used")
            ]

    idle, used = asyncio.run(scenario())
    assert idle is None
    assert used is not None


# The package's draft rules, against goldilocks-core's recorded capabilities.

CAPABILITIES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "app/src/__fixtures__/core/capabilities.json"
    ).read_text()
)


def test_a_new_draft_is_cores_first_code_and_task() -> None:
    draft = default_draft(CAPABILITIES)
    assert (draft.code, draft.task) == (
        CAPABILITIES["codes"][0]["id"],
        CAPABILITIES["tasks"][0]["id"],
    )
    assert (draft.hpc, draft.overrides, draft.structure) == (None, {}, None)
    assert draft.vocabulary == CAPABILITIES["vocabulary_version"]


def test_opening_another_structure_keeps_method_settings_only() -> None:
    """`walltime_h` describes the job and carries over; `ecutwfc_ry` is
    structure-specific and `is_metal` is a fact, so both described the old
    structure and are cleared, in draft order."""
    before = DftDraft(
        **{
            **DRAFT,
            "task": "phonon",
            "overrides": {"ecutwfc_ry": 80, "walltime_h": 24, "is_metal": True},
        }
    )
    fe = StructureInput(structure_content="data_Fe\n", structure_name="Fe.cif")
    opened, cleared = open_structure(before, fe, CAPABILITIES)
    assert opened.structure == fe
    assert opened.task == "phonon"
    assert opened.overrides == {"walltime_h": 24}
    assert cleared == ["ecutwfc_ry", "is_metal"]


def test_a_draft_from_another_vocabulary_starts_over() -> None:
    old = DftDraft(**{**DRAFT, "vocabulary": "4", "task": "phonon"})
    opened, cleared = open_structure(old, StructureInput(**SI), CAPABILITIES)
    assert (opened.task, opened.overrides, cleared) == ("scf_single_point", {}, [])


def test_a_request_sends_only_the_overrides_its_task_reads() -> None:
    settings = {setting["key"]: setting for setting in CAPABILITIES["settings"]}
    read_by_scf = next(
        k for k, s in settings.items() if "scf_single_point" in s["tasks"]
    )
    not_read = next(
        k for k, s in settings.items() if "scf_single_point" not in s["tasks"]
    )
    draft = DftDraft(
        **{**DRAFT, "overrides": {read_by_scf: 1, not_read: 2, "is_metal": True}}
    )
    request = compute_request(draft, CAPABILITIES)
    assert request == {
        **SI,
        "code": "quantum_espresso",
        "task": "scf_single_point",
        "hpc": None,
        "overrides": {read_by_scf: 1, "is_metal": True},
    }
    assert "fetch_missing" not in request


def test_structure_requests_and_keys() -> None:
    no_format = StructureInput(structure_content="x", structure_name="a.cif")
    assert structure_request(no_format) == {
        "structure_content": "x",
        "structure_name": "a.cif",
    }
    assert structure_key(StructureInput(**SI)) == structure_key(StructureInput(**SI))
    assert structure_key(no_format) != structure_key(
        StructureInput(structure_content="x", structure_name="b.cif")
    )
