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


def test_read_shared_deployment_enabled_env_var(monkeypatch) -> None:
    from goldilocks_agent.config import read_shared_deployment_enabled

    monkeypatch.delenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", raising=False)
    assert read_shared_deployment_enabled() is False
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    assert read_shared_deployment_enabled() is True
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "0")
    assert read_shared_deployment_enabled() is False


def test_shared_deployment_blocks_projects_and_conversations_routes(
    tmp_path, monkeypatch
) -> None:
    """Design doc §19.2: `projects`/`conversations` have no per-user column
    and never will -- on a shared server, listing "all rows" means listing
    everyone's history. This has to be refused at the route, not just left
    unused by the shared-mode frontend build."""
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    with _make_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/projects").status_code == 404
        create_response = client.post(
            "/api/projects", json={"name": "x", "color": "#fff"}
        )
        assert create_response.status_code == 404
        assert client.delete("/api/projects/some-id").status_code == 404
        assert client.get("/api/conversations").status_code == 404
        assert (
            client.patch(
                "/api/conversations/some-id", json={"project_id": None}
            ).status_code
            == 404
        )
        assert client.delete("/api/conversations/some-id").status_code == 404


def test_shared_deployment_skips_conversation_indexing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    calls = []

    async def fake_touch_conversation(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "goldilocks_agent.server.store.touch_conversation", fake_touch_conversation
    )
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/chat",
            json={
                "thread_id": "thread-1",
                "message": {"role": "user", "content": "Hello"},
            },
        )
        assert response.status_code == 200
    assert calls == []


def test_chat_concurrency_limit_returns_429_when_saturated(
    tmp_path, monkeypatch
) -> None:
    """Design doc §19.7: the real safe number needs measuring on the target
    VM, not guessing -- this only tests the mechanism (fail fast, don't
    queue), with the limit set to an arbitrary 1 for determinism."""
    monkeypatch.setenv("GOLDILOCKS_AGENT_CHAT_CONCURRENCY_LIMIT", "1")
    with _make_client(tmp_path, monkeypatch) as client:
        import goldilocks_agent.server as server_module

        monkeypatch.setattr(server_module, "_active_chat_requests", 1)
        response = client.post(
            "/api/chat",
            json={
                "thread_id": "thread-1",
                "message": {"role": "user", "content": "Hello"},
            },
        )
        assert response.status_code == 429


def test_chat_concurrency_limit_unset_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOLDILOCKS_AGENT_CHAT_CONCURRENCY_LIMIT", raising=False)
    with _make_client(tmp_path, monkeypatch) as client:
        import goldilocks_agent.server as server_module

        monkeypatch.setattr(server_module, "_active_chat_requests", 10_000)
        response = client.post(
            "/api/chat",
            json={
                "thread_id": "thread-1",
                "message": {"role": "user", "content": "Hello"},
            },
        )
        assert response.status_code == 200


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
