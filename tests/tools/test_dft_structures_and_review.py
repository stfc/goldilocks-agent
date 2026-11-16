"""The chat's structure files found by name, and what the model is told of
a draft, on the server (#142)."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from goldilocks_agent.core_api import CoreFailure
from goldilocks_agent.dft_draft import DftDraft
from goldilocks_agent.tools.dft_workbench.review import (
    failure_text,
    review_for_model,
    structure_summary,
)
from goldilocks_agent.tools.dft_workbench.structures import (
    TRUNCATED_ATTACHMENT_MARKER,
    chat_structures,
    infer_extension,
    missing_structure_text,
    structure_input,
)

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "src" / "__fixtures__" / "core"
SI = json.loads((FIXTURES / "structure.json").read_text())["structure_content"]


def _attached(name: str, content: str, lang: str = "cif") -> str:
    return f"[Attached file: {name}]\n```{lang}\n{content}```"


def _fetched(call_id: str, name: str, content: str) -> list:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_structure", "args": {}, "id": call_id}],
        ),
        ToolMessage(
            content=json.dumps({"filename": name, "content": content}),
            tool_call_id=call_id,
        ),
    ]


def test_attachments_and_fetched_structures_are_found_by_name() -> None:
    messages = [
        HumanMessage(content=f"Open this\n\n{_attached('Si.cif', SI)}"),
        *_fetched("c1", "mp-149.cif", SI),
        # A DFT output log is a file, not a structure.
        HumanMessage(
            content=_attached("pw.out", "Program PWSCF\n total energy -15.8\n", "text")
        ),
    ]
    found = chat_structures(messages)
    assert [(s.name, s.ext) for s in found] == [
        ("Si.cif", ".cif"),
        ("mp-149.cif", ".cif"),
    ]
    assert structure_input(found[0]).model_dump() == {
        "structure_content": SI,
        "structure_name": "Si.cif",
        "structure_format": "cif",
    }


def test_a_truncated_attachment_or_a_failed_fetch_is_no_structure() -> None:
    truncated = (
        f"{_attached('big.cif', SI)}\n{TRUNCATED_ATTACHMENT_MARKER} cut at 20000]"
    )
    failed = [
        AIMessage(
            content="", tool_calls=[{"name": "get_structure", "args": {}, "id": "c2"}]
        ),
        ToolMessage(content=json.dumps({"error": "not found"}), tool_call_id="c2"),
    ]
    assert chat_structures([HumanMessage(content=truncated), *failed]) == []


def test_only_get_structures_results_count_as_fetched_structures() -> None:
    other = [
        AIMessage(
            content="",
            tool_calls=[{"name": "find_in_databases", "args": {}, "id": "c3"}],
        ),
        ToolMessage(
            content=json.dumps({"filename": "x.cif", "content": SI}), tool_call_id="c3"
        ),
    ]
    assert chat_structures(other) == []


def test_a_multimodal_messages_text_part_carries_its_attachments() -> None:
    content = [
        {"type": "image_url", "image_url": {"url": "data:,"}},
        {"type": "text", "text": _attached("Si.cif", SI)},
    ]
    assert [s.name for s in chat_structures([HumanMessage(content=content)])] == [
        "Si.cif"
    ]


def test_a_name_attached_again_takes_the_latest_text() -> None:
    messages = [
        HumanMessage(content=_attached("Si.cif", "data_old\n")),
        HumanMessage(content=_attached("Si.cif", SI)),
    ]
    assert [(s.name, s.content) for s in chat_structures(messages)] == [("Si.cif", SI)]


def test_formats_are_inferred_from_the_name_else_the_text() -> None:
    poscar = "Si\n1.0\n5.4 0 0\n0 5.4 0\n0 0 5.4\nSi\n2\nDirect\n0 0 0\n"
    assert infer_extension("POSCAR", "") == ".poscar"
    assert infer_extension("structure", SI) == ".cif"
    assert infer_extension("structure", poscar) == ".poscar"
    assert infer_extension("notes.txt", "hello") == ".txt"


def test_a_missing_name_lists_the_names_there_are() -> None:
    found = chat_structures([HumanMessage(content=_attached("Si.cif", SI))])
    assert '"Si.cif"' in missing_structure_text("si.cif", found)
    assert "has no structure files yet" in missing_structure_text("x", [])


def test_the_review_keeps_values_and_sources_not_provenance() -> None:
    explain = json.loads((FIXTURES / "explain.json").read_text())
    draft = DftDraft(
        code="quantum_espresso", task="scf_single_point", overrides={"walltime_h": 24}
    )
    review = review_for_model(draft, explain)
    assert review["calculation"]["overrides"] == {"walltime_h": 24}
    assert set(review["records"]) == set(explain["records"])
    for record in review["records"].values():
        assert not {"field_sources", "citations", "model", "derived_from"} & set(record)
    assert "layout" not in review


def test_the_structure_summary_counts_partially_occupied_sites() -> None:
    inspection = json.loads((FIXTURES / "inspect.json").read_text())
    summary = structure_summary(inspection)
    assert summary["partially_occupied_sites"] == 0
    assert "sites" not in summary
    mixed = json.loads(json.dumps(inspection))
    mixed["structure"]["sites"][0]["species"][0]["occupancy"] = 0.5
    assert structure_summary(mixed)["partially_occupied_sites"] == 1


def test_a_failure_reads_its_findings_with_remedies_else_its_kind() -> None:
    blocked = CoreFailure(
        "advice_incomplete",
        "refused",
        status=422,
        details={
            "findings": [
                {
                    "summary": "No table covers Pu.",
                    "fields": [],
                    "remedies": [{"pseudo_table_id": "x"}],
                }
            ]
        },
    )
    assert (
        failure_text(blocked) == "advice_incomplete: No table covers Pu. "
        '(resolved by setting {"pseudo_table_id": "x"})'
    )
    internal = CoreFailure("internal_error", "boom", status=500)
    assert failure_text(internal).startswith(
        "internal_error: boom (goldilocks-core failed internally"
    )
