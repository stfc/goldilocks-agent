"""The website's chatbot and the local agent: what each profile offers."""

from __future__ import annotations

from goldilocks_agent.agent_profile import LOCAL, WEB, current_profile
from goldilocks_agent.tools import (
    DFT_WORKBENCH_TOOLS,
    MLIP_PLAYGROUND_TOOLS,
    STRUCTURE_SEARCH_TOOLS,
    TOOL_DISPATCH,
)


def test_the_website_offers_search_and_dft_workbench_only() -> None:
    assert WEB.tools == (*STRUCTURE_SEARCH_TOOLS, *DFT_WORKBENCH_TOOLS)
    assert not set(WEB.tools) & set(MLIP_PLAYGROUND_TOOLS)


def test_the_local_version_offers_the_websites_tools_and_mlip() -> None:
    assert set(LOCAL.tools) >= set(WEB.tools) | set(MLIP_PLAYGROUND_TOOLS)


def test_every_offered_tool_is_registered_in_its_order() -> None:
    for profile in (WEB, LOCAL):
        names = [schema["function"]["name"] for schema in profile.tool_schemas()]
        assert names == list(profile.tools)
        assert set(profile.tools) <= set(TOOL_DISPATCH)


def test_the_local_turn_is_longer_and_the_backstop_sits_above_each_budget() -> None:
    assert WEB.max_tool_calls_per_turn < LOCAL.max_tool_calls_per_turn
    # A turn spends at most two super-steps per tool call (tool, then llm),
    # one to refuse what's left and one to wrap up: the recursion limit only
    # bites when the budget somehow doesn't.
    for profile in (WEB, LOCAL):
        assert profile.recursion_limit > 2 * profile.max_tool_calls_per_turn + 2


def test_the_profile_follows_the_shared_deployment_setting(monkeypatch) -> None:
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "1")
    assert current_profile() is WEB
    monkeypatch.setenv("GOLDILOCKS_AGENT_SHARED_DEPLOYMENT", "0")
    assert current_profile() is LOCAL
