"""HTTP layer for projects/conversations (design doc 16.5) and the chat bridge.

Uses `GOLDILOCKS_AGENT_MODEL=not-a-real-provider/nope` so chat requests fail
fast inside litellm with no network call (see graph.resolve_model) --
these tests are about the HTTP contract and indexing side effects, not
about getting a real model reply (that's test_graph.py's job).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDILOCKS_AGENT_MODEL", "not-a-real-provider/nope")
    monkeypatch.setattr("goldilocks_agent.graph.DB_PATH", tmp_path / "goldilocks.db")
    monkeypatch.setattr("goldilocks_agent.store.DB_PATH", tmp_path / "goldilocks.db")
    from goldilocks_agent.server import app

    return TestClient(app)


def test_project_crud_round_trip(tmp_path, monkeypatch) -> None:
    with _make_client(tmp_path, monkeypatch) as client:
        created = client.post(
            "/api/projects", json={"name": "Perovskites", "color": "#3b82f6"}
        ).json()
        assert created["name"] == "Perovskites"

        listed = client.get("/api/projects").json()
        assert [p["id"] for p in listed] == [created["id"]]

        client.delete(f"/api/projects/{created['id']}")
        assert client.get("/api/projects").json() == []


def test_chat_indexes_conversation_even_when_model_call_fails(
    tmp_path, monkeypatch
) -> None:
    with _make_client(tmp_path, monkeypatch) as client:
        project = client.post(
            "/api/projects", json={"name": "Perovskites", "color": "#3b82f6"}
        ).json()

        response = client.post(
            "/api/chat",
            json={
                "thread_id": "thread-1",
                "message": {"role": "user", "content": "Hello"},
                "title": "Hello",
                "project_id": project["id"],
            },
        )
        assert response.status_code == 200
        # the model error surfaced in the stream, not swallowed
        assert "not-a-real-provider" in response.text

        conversations = client.get("/api/conversations").json()
        assert len(conversations) == 1
        assert conversations[0]["id"] == "thread-1"
        assert conversations[0]["project_id"] == project["id"]
        assert conversations[0]["title"] == "Hello"


def test_mlip_endpoint_degrades_clearly_when_not_configured(
    tmp_path, monkeypatch
) -> None:
    """No GOLDILOCKS_AGENT_MLIP_ENABLED in a fresh dev environment -- real
    code path, not a mock, exercising `client._require_enabled()`'s own
    RuntimeError."""
    monkeypatch.delenv("GOLDILOCKS_AGENT_MLIP_ENABLED", raising=False)
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/mlip/singlepoint",
            json={"structure_content": "not a real cif", "structure_name": "x.cif"},
        )
        assert response.status_code == 503
        assert "GOLDILOCKS_AGENT_MLIP_ENABLED" in response.json()["detail"]


def test_conversation_reassign_and_delete_clears_checkpointer(
    tmp_path, monkeypatch
) -> None:
    with _make_client(tmp_path, monkeypatch) as client:
        project_a = client.post(
            "/api/projects", json={"name": "A", "color": "#3b82f6"}
        ).json()
        project_b = client.post(
            "/api/projects", json={"name": "B", "color": "#ef4444"}
        ).json()
        client.post(
            "/api/chat",
            json={
                "thread_id": "thread-1",
                "message": {"role": "user", "content": "Hello"},
                "project_id": project_a["id"],
            },
        )

        client.patch(
            "/api/conversations/thread-1", json={"project_id": project_b["id"]}
        )
        conversations = client.get("/api/conversations").json()
        assert conversations[0]["project_id"] == project_b["id"]

        client.delete("/api/conversations/thread-1")
        assert client.get("/api/conversations").json() == []

        history = client.get("/api/chat/thread-1").json()
        assert history["messages"] == []


def test_pending_interrupt_event_distinguishes_client_execute() -> None:
    from goldilocks_agent.server import _pending_interrupt_event

    confirmation = {"tool": "dft_download_bundle", "args": {}, "label": "y"}
    assert _pending_interrupt_event(confirmation) == "confirmation_needed"
    assert (
        _pending_interrupt_event(
            {"tool": "dft_review", "args": {}, "client_execute": True}
        )
        == "client_tool_call"
    )


def test_core_proxy_forwards_every_registered_path(tmp_path, monkeypatch) -> None:
    """Without this route, every one of these calls 404s/405s against
    goldilocks-agent's own SPA instead of ever reaching goldilocks-core --
    confirmed live 2026-09-25 in a real Docker deployment (`GET
    /capabilities` -> 404, `POST /inspect` -> 405). No real core process
    involved here -- `httpx.AsyncClient.request` is faked so this only
    checks the forwarding contract (method/path/body/response passthrough),
    not core's own behavior."""
    import httpx

    from goldilocks_agent import core_server

    calls = []

    async def fake_request(self, method, url, content=None, headers=None):
        calls.append((method, url, content))
        return httpx.Response(
            200,
            content=b'{"ok": true}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    with _make_client(tmp_path, monkeypatch) as client:
        for path in core_server.CORE_PROXIED_PATHS:
            response = client.get(path)
            assert response.status_code == 200
            assert response.json() == {"ok": True}

        response = client.post("/inspect", content=b'{"a": 1}')
        assert response.status_code == 200

    assert ("GET", f"{core_server.CORE_SERVER_BASE_URL}/capabilities", b"") in calls
    assert (
        "POST",
        f"{core_server.CORE_SERVER_BASE_URL}/inspect",
        b'{"a": 1}',
    ) in calls


def test_core_proxy_returns_a_clean_503_while_core_is_still_starting(
    tmp_path, monkeypatch
) -> None:
    """goldilocks-core is lazy-started and, with mMACE enabled, its cold
    start can take several minutes -- a request that lands during that
    window used to leak an unhandled `httpx.ConnectError` as a bare 500."""
    import httpx

    async def fake_request(self, method, url, content=None, headers=None):
        raise httpx.ConnectError("Connection refused", request=None)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    with _make_client(tmp_path, monkeypatch) as client:
        response = client.get("/capabilities")
        assert response.status_code == 503
        assert "still be starting" in response.json()["detail"]
