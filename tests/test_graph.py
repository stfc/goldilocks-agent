"""End-to-end smoke test for the minimal graph (design doc 7.1: smoke test layer).

Asserts the graph produces *a* reply and that checkpointed state accumulates
correctly across turns -- not what the model says. Free-form LLM output isn't
a stable invariant to assert on (AGENTS.md); message-count growth under a
shared `thread_id` is a structural invariant (design doc 16: checkpointer is
the only place conversation state lives).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import struct
import urllib.request
import zlib

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from goldilocks_agent.config import get_api_key
from goldilocks_agent.graph import (
    LOCAL_MODEL,
    _repair_orphaned_tool_calls,
    build_graph,
    experience_level_system_message,
)

_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def _local_model_available() -> bool:
    tag = LOCAL_MODEL.removeprefix("ollama_chat/")
    try:
        with urllib.request.urlopen(_OLLAMA_TAGS_URL, timeout=1) as resp:
            names = [m["name"] for m in json.load(resp)["models"]]
    except OSError:
        return False
    return tag in names


requires_local_model = pytest.mark.skipif(
    not _local_model_available(), reason="local Ollama model not pulled/running"
)

# Tool-calling is validated against the cloud model, not the local one --
# implementation plan Step 3 deliberately separates "is the graph shape
# right" from "does this specific model/provider handle tool-calling well
# through litellm," and the cloud key is already verified working (graph.py).
requires_anthropic_key = pytest.mark.skipif(
    not get_api_key("anthropic"), reason="no Anthropic API key configured"
)

requires_mlip_enabled = pytest.mark.skipif(
    not os.environ.get("GOLDILOCKS_AGENT_MLIP_ENABLED"),
    reason="GOLDILOCKS_AGENT_MLIP_ENABLED not set -- MLIP Playground opt-in",
)

requires_core_cli = pytest.mark.skipif(
    not os.environ.get("GOLDILOCKS_CORE_PATH"),
    reason="GOLDILOCKS_CORE_PATH not configured -- DFT Workspace opt-in, see config.py",
)

_TEST_NACL_CIF = """\
data_NaCl
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M "P 1"
loop_
_atom_site_label
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na 0.0 0.0 0.0
Cl 0.5 0.5 0.5
"""

# Same fixture as tests/tools/test_mlip_playground.py's `_AL_VACANCY_INIT_CIF`/
# `_AL_VACANCY_FINAL_CIF` (duplicated here rather than imported, matching how
# `_TEST_NACL_CIF` above is already its own copy, not shared with that
# file) -- smallest real two-endpoint NEB case: a nearest-neighbour vacancy
# hop in a 2x1x1 FCC Al supercell. See that file's comment for how/why this
# was built and verified against the real `janus neb` CLI (barrier ~0.76 eV,
# the right ballpark for known Al vacancy-migration energies).
_TEST_AL_VACANCY_INIT_CIF = """\
data_image0
_chemical_formula_structural       Al7
_chemical_formula_sum              "Al7"
_cell_length_a       8.1
_cell_length_b       4.05
_cell_length_c       4.05
_cell_angle_alpha    90.0
_cell_angle_beta     90.0
_cell_angle_gamma    90.0

_space_group_name_H-M_alt    "P 1"
_space_group_IT_number       1

loop_
  _space_group_symop_operation_xyz
  'x, y, z'

loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_fract_x
  _atom_site_fract_y
  _atom_site_fract_z
  _atom_site_occupancy
  Al  Al1       1.0  0.0  0.5  0.5  1.0000
  Al  Al2       1.0  0.25  0.0  0.5  1.0000
  Al  Al3       1.0  0.25  0.5  0.0  1.0000
  Al  Al4       1.0  0.5  0.0  0.0  1.0000
  Al  Al5       1.0  0.5  0.5  0.5  1.0000
  Al  Al6       1.0  0.75  0.0  0.5  1.0000
  Al  Al7       1.0  0.75  0.5  0.0  1.0000
"""

_TEST_AL_VACANCY_FINAL_CIF = """\
data_image0
_chemical_formula_structural       Al7
_chemical_formula_sum              "Al7"
_cell_length_a       8.1
_cell_length_b       4.05
_cell_length_c       4.05
_cell_angle_alpha    90.0
_cell_angle_beta     90.0
_cell_angle_gamma    90.0

_space_group_name_H-M_alt    "P 1"
_space_group_IT_number       1

loop_
  _space_group_symop_operation_xyz
  'x, y, z'

loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_fract_x
  _atom_site_fract_y
  _atom_site_fract_z
  _atom_site_occupancy
  Al  Al1       1.0  0.0  0.0  0.0  1.0000
  Al  Al2       1.0  0.25  0.0  0.5  1.0000
  Al  Al3       1.0  0.25  0.5  0.0  1.0000
  Al  Al4       1.0  0.5  0.0  0.0  1.0000
  Al  Al5       1.0  0.5  0.5  0.5  1.0000
  Al  Al6       1.0  0.75  0.0  0.5  1.0000
  Al  Al7       1.0  0.75  0.5  0.0  1.0000
"""


def _solid_color_png_data_url(rgb: tuple[int, int, int], size: int = 32) -> str:
    """Build a solid-color PNG by hand -- no image library dependency needed
    just to prove a real color reaches the model as an actual image."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = bytes([0]) + bytes(rgb) * size
    idat = zlib.compress(row * size)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"


@pytest.mark.integration
@requires_local_model
def test_llm_node_replies() -> None:
    graph = build_graph()
    messages = [{"role": "user", "content": "Say hello in one word."}]
    result = asyncio.run(graph.ainvoke({"messages": messages}))
    reply = result["messages"][-1]
    assert reply.content.strip()


@pytest.mark.integration
@requires_local_model
def test_checkpointer_carries_history_across_turns() -> None:
    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "test-thread"}}

    async def run() -> list:
        first = {"messages": [{"role": "user", "content": "hi"}]}
        await graph.ainvoke(first, config=config)
        second = {"messages": [{"role": "user", "content": "hi again"}]}
        result = await graph.ainvoke(second, config=config)
        return result["messages"]

    messages = asyncio.run(run())
    # Second call sent only its own new message -- the four messages below
    # (2 user + 2 assistant) can only be here if the checkpointer, not the
    # caller, supplied the first turn's history.
    assert len(messages) == 4


@pytest.mark.integration
@requires_local_model
def test_llm_node_reads_multimodal_image_content() -> None:
    """Qwen3.8 is vision-capable (verified 2026-09-15 against the real model)
    and `app/`'s image-attach path sends OpenAI-style content parts through
    this same node -- assert the model actually sees the image, not just
    that it replies (a broken pipe could still hallucinate *some* text)."""
    graph = build_graph()
    message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "What color is this image? Answer with one word.",
            },
            {
                "type": "image_url",
                "image_url": {"url": _solid_color_png_data_url((220, 20, 20))},
            },
        ],
    }
    result = asyncio.run(graph.ainvoke({"messages": [message]}))
    assert "red" in result["messages"][-1].content.lower()


@pytest.mark.integration
@requires_anthropic_key
def test_llm_node_calls_find_in_databases_tool() -> None:
    """First real exercise of the llm<->tool loop (graph.py's `route_after_llm`
    / `call_tool`) -- a real model call, not a stubbed tool_calls response, so
    a subtly wrong tool schema or a broken OpenAI dict round-trip through
    `MessagesState` would actually surface here."""
    graph = build_graph()
    config = {"configurable": {"model_id": "anthropic-claude"}}
    message = {
        "role": "user",
        "content": (
            "Use the find_in_databases tool to check whether NaCl has an "
            "existing computed structure. Report what you find."
        ),
    }
    result = asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    messages = result["messages"]

    tool_messages = [m for m in messages if m.type == "tool"]
    assert tool_messages, "expected the graph to have routed through the tool node"
    payload = json.loads(tool_messages[0].content)
    assert "error" not in payload  # top-level dispatch failure, not the `errors` field
    assert payload["query_formula"] == "NaCl"
    assert payload["groups"], "NaCl is a real formula -- some source should hit"

    reply = messages[-1].content.lower()
    assert "nacl" in reply or "sodium chloride" in reply


@pytest.mark.integration
@requires_anthropic_key
@requires_core_cli
def test_llm_node_calls_dft_explain_tool() -> None:
    """First real exercise of DFT Workspace's LLM tool-calling path (added
    2026-09-16, previously panel-only) -- no confirmation gate (see
    `dft_workspace/tool.py`'s own docstring for why), so this mirrors
    find_in_databases's test shape above, not MLIP's pause-for-confirmation
    one below."""
    graph = build_graph()
    config = {"configurable": {"model_id": "anthropic-claude"}}
    message = {
        "role": "user",
        "content": (
            f"Here is a CIF for NaCl:\n\n{_TEST_NACL_CIF}\n\n"
            "Use the dft_explain tool (structure_name 'NaCl.cif') to see what "
            "DFT settings goldilocks-core would recommend for this structure."
        ),
    }
    result = asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    messages = result["messages"]

    tool_messages = [m for m in messages if m.type == "tool"]
    assert tool_messages, "expected the graph to have routed through the tool node"
    payload = json.loads(tool_messages[-1].content)
    assert "error" not in payload
    assert payload["records"]["cutoffs"]["status"] == "resolved"


@pytest.mark.integration
@requires_anthropic_key
@requires_core_cli
def test_llm_node_calls_dft_generate_tool_and_trims_pseudo_for_llm() -> None:
    """Confirms `RunResult.model_dump_for_llm()` (models.py) actually reaches
    the LLM's own copy of the tool result through `call_tool`'s generic
    `model_dump_for_llm()`-preference hook, not just in isolation."""
    graph = build_graph()
    config = {"configurable": {"model_id": "anthropic-claude"}}
    message = {
        "role": "user",
        "content": (
            f"Here is a CIF for NaCl:\n\n{_TEST_NACL_CIF}\n\n"
            "Use the dft_generate tool (structure_name 'NaCl.cif') to generate "
            "a real Quantum ESPRESSO input for a single-point SCF calculation."
        ),
    }
    result = asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    messages = result["messages"]

    tool_messages = [m for m in messages if m.type == "tool"]
    assert tool_messages, "expected the graph to have routed through the tool node"
    payload = json.loads(tool_messages[-1].content)
    assert "error" not in payload
    assert "scf.in" in payload["files"]
    assert not any(name.startswith("pseudo/") for name in payload["files"])


@pytest.mark.integration
@requires_anthropic_key
def test_mlip_tool_call_pauses_for_confirmation_and_can_be_declined() -> None:
    """First real exercise of `call_tool`'s interrupt gate (graph.py,
    2026-09-15) -- a real model call asked to run a confirmation-required
    tool must leave the graph paused (not silently execute it), and
    declining must feed the LLM a clear "user said no", not a crash. No
    GOLDILOCKS_AGENT_MLIP_ENABLED needed: declining short-circuits before
    the tool ever actually dispatches to the `janus` CLI.
    """
    graph = build_graph(InMemorySaver())
    config = {
        "configurable": {"thread_id": "mlip-decline", "model_id": "anthropic-claude"}
    }
    message = {
        "role": "user",
        "content": (
            f"Here is a CIF for NaCl:\n\n{_TEST_NACL_CIF}\n\n"
            "Use the run_mlip_singlepoint tool (structure_name 'NaCl.cif') "
            "to run a MACE single-point calculation on it."
        ),
    }
    asyncio.run(graph.ainvoke({"messages": [message]}, config=config))

    paused = asyncio.run(graph.aget_state(config))
    assert paused.next, "expected the graph to pause for confirmation"
    pending = paused.tasks[0].interrupts[0].value
    assert pending["tool"] == "run_mlip_singlepoint"
    assert "label" in pending

    final = asyncio.run(
        graph.ainvoke(Command(resume={"approved": False}), config=config)
    )
    tool_messages = [m for m in final["messages"] if m.type == "tool"]
    assert tool_messages
    payload = json.loads(tool_messages[-1].content)
    assert "declined" in payload["error"].lower()

    resumed_state = asyncio.run(graph.aget_state(config))
    assert not resumed_state.next, "graph should have run to completion after resuming"


def test_repair_orphaned_tool_calls_fills_in_missing_tool_results() -> None:
    """2026-09-16 incident: a user typing a new message instead of answering
    a pending confirmation card leaves an assistant `tool_calls` message
    permanently unanswered in the checkpointer (LangGraph's own resume rule
    treats a plain new message as a fresh run, discarding the still-paused
    tool call -- see this function's own docstring in graph.py). Every
    provider rejects that history outright, forever, once it's persisted --
    this is the self-healing repair `call_llm` now runs on every call."""
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "a", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "b", "arguments": "{}"},
                },
            ],
        },
        {"role": "user", "content": "a new message sent before either call resolved"},
    ]

    repaired = _repair_orphaned_tool_calls(messages)

    # Both tool_call ids must be answered *immediately* after the assistant
    # message that made them -- exactly what Anthropic/OpenAI require.
    assert repaired[0] == messages[0]
    assert repaired[1] == messages[1]
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "call_1"
    assert "interrupted" in json.loads(repaired[2]["content"])["error"]
    assert repaired[3]["role"] == "tool"
    assert repaired[3]["tool_call_id"] == "call_2"
    assert repaired[4] == messages[2]


def test_repair_orphaned_tool_calls_leaves_well_formed_history_untouched() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "a", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]
    assert _repair_orphaned_tool_calls(messages) == messages


def test_repair_orphaned_tool_calls_only_backfills_the_id_that_never_landed() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "a", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "b", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        {"role": "user", "content": "next turn"},
    ]

    repaired = _repair_orphaned_tool_calls(messages)

    assert repaired[1] == messages[1]  # the real call_1 result, untouched
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "call_2"
    assert repaired[3] == messages[2]


@pytest.mark.integration
@requires_anthropic_key
def test_new_message_while_confirmation_pending_self_heals_instead_of_crashing() -> (
    None
):
    """End-to-end reproduction of the 2026-09-16 incident: the user ignores a
    pending confirmation card and sends a brand-new message instead. Before
    `_repair_orphaned_tool_calls`, this made every subsequent turn on the
    thread fail identically with litellm.BadRequestError forever (the
    orphaned tool_calls message is permanent once checkpointed) -- this
    proves the graph now stays usable instead.
    """
    graph = build_graph(InMemorySaver())
    config = {
        "configurable": {
            "thread_id": "mlip-typed-ahead",
            "model_id": "anthropic-claude",
        }
    }
    message = {
        "role": "user",
        "content": (
            f"Here is a CIF for NaCl:\n\n{_TEST_NACL_CIF}\n\n"
            "Use the run_mlip_singlepoint tool (structure_name 'NaCl.cif') "
            "to run a MACE single-point calculation on it."
        ),
    }
    asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    paused = asyncio.run(graph.aget_state(config))
    assert paused.next, "expected the graph to pause for confirmation"

    # The user never answers the card -- asks something unrelated instead.
    follow_up = {
        "role": "user",
        "content": "Never mind, what's the melting point of NaCl?",
    }
    result = asyncio.run(graph.ainvoke({"messages": [follow_up]}, config=config))
    reply = result["messages"][-1]
    assert reply.content.strip(), "must still answer, not crash on malformed history"

    # And the thread must stay usable afterwards, not fail identically forever.
    again = asyncio.run(
        graph.ainvoke(
            {"messages": [{"role": "user", "content": "And NaCl's density?"}]},
            config=config,
        )
    )
    assert again["messages"][-1].content.strip()


@pytest.mark.integration
@requires_anthropic_key
@requires_mlip_enabled
def test_mlip_tool_call_runs_for_real_once_approved() -> None:
    graph = build_graph(InMemorySaver())
    config = {
        "configurable": {"thread_id": "mlip-approve", "model_id": "anthropic-claude"}
    }
    message = {
        "role": "user",
        "content": (
            f"Here is a CIF for NaCl:\n\n{_TEST_NACL_CIF}\n\n"
            "Use the run_mlip_singlepoint tool (structure_name 'NaCl.cif') "
            "to run a MACE single-point calculation on it."
        ),
    }
    asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    final = asyncio.run(
        graph.ainvoke(Command(resume={"approved": True}), config=config)
    )

    tool_messages = [m for m in final["messages"] if m.type == "tool"]
    assert tool_messages
    payload = json.loads(tool_messages[-1].content)
    assert "error" not in payload
    assert payload["energy"] is not None


@pytest.mark.integration
@requires_anthropic_key
@requires_mlip_enabled
def test_mlip_neb_tool_call_runs_for_real_and_llm_reports_the_real_barrier() -> None:
    """NEB's first real end-to-end run (previously only checked against
    source, see acceptance-testing task) -- a real Claude call drives
    `run_mlip_neb` through the same confirm/approve/execute path as
    singlepoint above, on the smallest legitimate two-endpoint NEB case (an
    Al FCC nearest-neighbour vacancy hop, real barrier verified directly
    against the client function beforehand: ~0.7647 eV, deterministic --
    MACE/LBFGS has no randomness). Confirms both that the LLM can drive a
    calc type it's never been exercised on before, and that its own
    follow-up text reports a number in the right ballpark rather than a
    hallucinated one.
    """
    graph = build_graph(InMemorySaver())
    config = {
        "configurable": {
            "thread_id": "mlip-neb-approve",
            "model_id": "anthropic-claude",
        }
    }
    message = {
        "role": "user",
        "content": (
            f"Here is the initial structure (CIF) for an aluminium "
            f"vacancy hop:\n\n{_TEST_AL_VACANCY_INIT_CIF}\n\n"
            f"Here is the final structure (CIF), after the hop:\n\n"
            f"{_TEST_AL_VACANCY_FINAL_CIF}\n\n"
            "Use the run_mlip_neb tool (init_structure_name "
            "'Al_vacancy_init.cif', final_structure_name "
            "'Al_vacancy_final.cif', n_images=3, fmax=0.5 -- keep it small, "
            "this is just a quick check) to estimate the migration barrier "
            "between them. Once you have a result, report the barrier in "
            "eV back to me."
        ),
    }
    asyncio.run(graph.ainvoke({"messages": [message]}, config=config))
    final = asyncio.run(
        graph.ainvoke(Command(resume={"approved": True}), config=config)
    )

    tool_messages = [m for m in final["messages"] if m.type == "tool"]
    assert tool_messages
    payload = json.loads(tool_messages[-1].content)
    assert "error" not in payload
    assert payload["barrier"] == pytest.approx(0.7647223845317481, abs=1e-3)

    reply = final["messages"][-1].content
    assert reply.strip()
    numbers = [float(n) for n in re.findall(r"-?\d+\.\d+", reply)]
    assert any(abs(n - payload["barrier"]) < 0.05 for n in numbers), (
        f"expected the reply to report the real barrier (~0.76 eV), got: {reply!r}"
    )


def test_experience_level_system_message_shape() -> None:
    assert experience_level_system_message(None) is None
    assert experience_level_system_message("nonsense") is None
    for level in ("new", "familiar", "advanced"):
        message = experience_level_system_message(level)
        assert message["role"] == "system"
        # Design doc 12.2: this must never look like it changes recommended
        # values/parameters, only explanation depth -- the guardrail sentence
        # is the mechanism for that, so its presence is worth asserting on.
        assert "never change which values" in message["content"]


@pytest.mark.integration
@requires_local_model
def test_experience_level_changes_response_length() -> None:
    """Not just "the system message gets built" -- confirm it actually
    reaches the model and visibly changes its behavior. "new" is instructed
    to explain more; "advanced" is instructed to be concise -- a real
    response to the same question should reflect that, not just parrot the
    instruction back."""
    question = "What is density functional theory?"

    def ask(level: str) -> str:
        graph = build_graph()
        config = {"configurable": {"experience_level": level}}
        result = asyncio.run(
            graph.ainvoke(
                {"messages": [{"role": "user", "content": question}]}, config=config
            )
        )
        return result["messages"][-1].content

    new_reply = ask("new")
    advanced_reply = ask("advanced")

    assert len(new_reply) > len(advanced_reply)
