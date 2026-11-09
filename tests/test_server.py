"""HTTP layer for projects/conversations (design doc 16.5) and the chat bridge.

Uses `GOLDILOCKS_AGENT_MODEL=not-a-real-provider/nope` so chat requests fail
fast inside litellm with no network call (see graph.resolve_model) --
these tests are about the HTTP contract and indexing side effects, not
about getting a real model reply (that's test_graph.py's job).
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDILOCKS_AGENT_MODEL", "not-a-real-provider/nope")
    monkeypatch.setattr("goldilocks_agent.graph.DB_PATH", tmp_path / "goldilocks.db")
    monkeypatch.setattr("goldilocks_agent.store.DB_PATH", tmp_path / "goldilocks.db")
    from goldilocks_agent.server import app

    return TestClient(app)


def _stub_litellm(monkeypatch) -> list[dict]:
    """Replaces `litellm.acompletion` with a stream that calls `dft_review`
    on the first call and replies "Reviewed." after that. Returns the list
    of calls it gets, each as its `model` and `messages` arguments."""
    calls: list[dict] = []

    def chunk(content=None, tool_calls=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    async def acompletion(*, model, messages, **_):
        calls.append({"model": model, "messages": messages})
        if len(calls) == 1:
            call = SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="dft_review", arguments="{}"),
            )
            chunks = [chunk(tool_calls=[call])]
        else:
            chunks = [chunk(content="Reviewed.")]

        async def stream():
            for c in chunks:
                yield c

        return stream()

    monkeypatch.setattr("goldilocks_agent.graph.litellm.acompletion", acompletion)
    monkeypatch.setattr("goldilocks_agent.graph.get_api_key", lambda provider: "k")
    return calls


def _client_tool_turn(client, model_id: str, **fields) -> None:
    """Sends a turn that pauses on the client-executed `dft_review`, then
    resumes it with a result, both with `model_id` and any other request
    `fields` -- what the browser sends."""
    first = client.post(
        "/api/chat",
        json={
            "thread_id": "t",
            "message": {"role": "user", "content": "Review it."},
            "model_id": model_id,
            **fields,
        },
    )
    assert "event: client_tool_call" in first.text
    resumed = client.post(
        "/api/chat",
        json={
            "thread_id": "t",
            "resume": {"result": {}},
            "model_id": model_id,
            **fields,
        },
    )
    assert "Reviewed." in resumed.text


def _system_prompts(calls: list[dict]) -> list[str]:
    """The system message content of each model call, which must come first."""
    for call in calls:
        assert call["messages"][0]["role"] == "system"
        assert all(m["role"] != "system" for m in call["messages"][1:])
    return [call["messages"][0]["content"] for call in calls]


def test_local_build_resume_runs_on_the_selected_model(tmp_path, monkeypatch) -> None:
    """#14: a local build has no model override, so a resume after a client
    tool call runs on the `model_id` it carries, not the local fallback."""
    with _make_client(tmp_path, monkeypatch) as client:
        monkeypatch.delenv("GOLDILOCKS_AGENT_MODEL")
        calls = _stub_litellm(monkeypatch)
        _client_tool_turn(client, "anthropic-claude")
    assert [c["model"] for c in calls] == [
        "anthropic/claude-sonnet-5",
        "anthropic/claude-sonnet-5",
    ]


def test_web_build_resume_stays_on_the_pinned_model(tmp_path, monkeypatch) -> None:
    """The shared web build answers with its pinned local model only
    (`GOLDILOCKS_AGENT_MODEL` in deploy/stfc-cloud/docker-compose.yml),
    whatever `model_id` a request names -- resumes included."""
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    with _make_client(tmp_path, monkeypatch) as client:
        monkeypatch.setenv("GOLDILOCKS_AGENT_MODEL", "ollama_chat/qwen3.8:27b")
        calls = _stub_litellm(monkeypatch)
        _client_tool_turn(client, "anthropic-claude")
    assert [c["model"] for c in calls] == [
        "ollama_chat/qwen3.8:27b",
        "ollama_chat/qwen3.8:27b",
    ]


def test_every_model_call_starts_with_the_system_prompt_and_never_stores_it(
    tmp_path, monkeypatch
) -> None:
    """#72: the system prompt goes first on every call, the resume after a
    client tool call included, and never into the checkpointer -- a
    reworded prompt reaches old chats too."""
    from goldilocks_agent.graph import system_message

    monkeypatch.setattr("goldilocks_agent.server.read_experience_level", lambda: None)
    with _make_client(tmp_path, monkeypatch) as client:
        calls = _stub_litellm(monkeypatch)
        _client_tool_turn(client, "local")
        history = client.get("/api/chat/t").json()["messages"]
    assert _system_prompts(calls) == [system_message(None)["content"]] * 2
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]


def test_local_build_pitches_the_prompt_at_the_saved_experience_level(
    tmp_path, monkeypatch
) -> None:
    """A local build reads its own saved preference (design doc 12.2), not
    a level a request names."""
    from goldilocks_agent.graph import system_message

    monkeypatch.setattr("goldilocks_agent.server.read_experience_level", lambda: "new")
    with _make_client(tmp_path, monkeypatch) as client:
        calls = _stub_litellm(monkeypatch)
        _client_tool_turn(client, "local", experience_level="advanced")
    assert _system_prompts(calls) == [system_message("new")["content"]] * 2


def test_web_build_pitches_the_prompt_at_the_level_the_tab_sends(
    tmp_path, monkeypatch
) -> None:
    """The shared deployment has no per-user place to keep the setting, so
    each tab sends its own (§19.2); the one server-side file is never read
    there, and its routes are refused."""
    from goldilocks_agent.graph import system_message

    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    monkeypatch.setattr("goldilocks_agent.server.read_experience_level", lambda: "new")
    with _make_client(tmp_path, monkeypatch) as client:
        calls = _stub_litellm(monkeypatch)
        _client_tool_turn(client, "local", experience_level="advanced")
        assert client.get("/api/preferences").status_code == 404
        saved = client.post("/api/preferences", json={"experience_level": "new"})
        assert saved.status_code == 404
    assert _system_prompts(calls) == [system_message("advanced")["content"]] * 2


def test_chat_rejects_an_unknown_experience_level(tmp_path, monkeypatch) -> None:
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/chat",
            json={
                "thread_id": "t",
                "message": {"role": "user", "content": "Hi"},
                "experience_level": "expert",
            },
        )
    assert response.status_code == 422


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


def test_shared_deployment_blocks_credentials_routes(tmp_path, monkeypatch) -> None:
    """`~/.config/goldilocks/config.toml` is one file on the server, not a
    per-thread/per-user store -- letting any visitor write to it on a shared
    server would mean the first person who opens Settings sets everyone
    else's provider key (their own account gets billed for the whole
    instance's traffic, silently). Refused at the route, not just left
    unused by the shared-mode frontend build."""
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    with _make_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/credentials").status_code == 404
        save_response = client.post(
            "/api/credentials", json={"provider": "anthropic", "api_key": "sk-test"}
        )
        assert save_response.status_code == 404


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


def test_mlip_routes_404_while_unreleased(tmp_path, monkeypatch) -> None:
    """MLIP Playground is "Coming soon" (`MLIP_PLAYGROUND_RELEASED`) -- every
    `/api/mlip/*` route must refuse before touching the `janus` CLI, even
    with GOLDILOCKS_AGENT_MLIP_ENABLED on (as the local docker-compose.yml
    has it)."""
    monkeypatch.setattr("goldilocks_agent.server.MLIP_PLAYGROUND_RELEASED", False)
    monkeypatch.setenv("GOLDILOCKS_AGENT_MLIP_ENABLED", "1")

    # A gate regression must fail here, not start a multi-GB `uv sync` of
    # ./mlip-cli on the way to the real CLI.
    async def _cli_reached(*args):
        raise AssertionError(f"MLIP gate bypassed: janus CLI reached with {args}")

    monkeypatch.setattr(
        "goldilocks_agent.tools.mlip_playground.client._run_in_mlip_cli", _cli_reached
    )
    structure = {"structure_content": "not a real cif", "structure_name": "x.cif"}
    neb = {
        "init_structure_content": "x",
        "init_structure_name": "a.cif",
        "final_structure_content": "y",
        "final_structure_name": "b.cif",
    }
    with _make_client(tmp_path, monkeypatch) as client:
        for path, body in [
            ("/api/mlip/singlepoint", structure),
            ("/api/mlip/geomopt", structure),
            ("/api/mlip/eos", structure),
            ("/api/mlip/neb", neb),
            ("/api/mlip/phonons", structure),
        ]:
            assert client.post(path, json=body).status_code == 404, path


def test_mlip_endpoint_degrades_clearly_when_not_configured(
    tmp_path, monkeypatch
) -> None:
    """No GOLDILOCKS_AGENT_MLIP_ENABLED in a fresh dev environment -- real
    code path, not a mock, exercising `client._require_enabled()`'s own
    RuntimeError. Forced released so this keeps covering the route body
    while MLIP Playground is "Coming soon"."""
    monkeypatch.setattr("goldilocks_agent.server.MLIP_PLAYGROUND_RELEASED", True)
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
        # Core's own envelope, the only error shape the Workbench's
        # HttpCoreClient reads its message from.
        error = response.json()["error"]
        assert error["kind"] == "core_unavailable"
        assert "still be starting" in error["message"]
