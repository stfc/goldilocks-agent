"""What the chat's dft_settings and dft_set read and change, on the server
(#142): #137's browser cases (app/src/dftWorkbench.test.ts), ported one for
one against the same recorded goldilocks-core answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goldilocks_agent.dft_draft import DftDraft, StructureInput, compute_request
from goldilocks_agent.tools.dft_workbench.settings import (
    DraftChanges,
    apply_changes,
    describe_changes,
    setting_changes,
    settings_catalogue,
)

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "src" / "__fixtures__" / "core"
CAPS = json.loads((FIXTURES / "capabilities.json").read_text())
INSPECTION = json.loads((FIXTURES / "inspect.json").read_text())
EXPLAIN = json.loads((FIXTURES / "explain.json").read_text())
STRUCTURE = StructureInput(**json.loads((FIXTURES / "structure.json").read_text()))


def _draft(**fields) -> DftDraft:
    return DftDraft(
        vocabulary="5",
        structure=STRUCTURE,
        code="quantum_espresso",
        task="scf_single_point",
        hpc=None,
        overrides=fields.pop("overrides", {}),
        **fields,
    )


def _set(values, draft: DftDraft | None = None, inspection=INSPECTION) -> DraftChanges:
    return setting_changes(values, CAPS, draft or _draft(), inspection)


def _refusal(values, draft: DftDraft | None = None, inspection=INSPECTION) -> str:
    with pytest.raises(ValueError) as refused:
        _set(values, draft, inspection)
    return str(refused.value)


def test_sets_any_panel_setting_and_null_puts_it_back_to_automatic() -> None:
    values = {
        "walltime_h": 24,
        "smearing_type": "cold",
        "k_grid": [8, 8, 8],
        "is_metal": "metal",
        "use_vdw": True,
    }
    changes = _set(values)
    assert changes == DraftChanges(overrides=values)
    draft = apply_changes(_draft(), changes)
    assert draft.overrides == values
    assert compute_request(draft, CAPS)["overrides"] == values

    reset = _set({"k_grid": None}, draft)
    assert describe_changes(reset) == {"k_grid": "automatic"}
    # Cleared, not sent as null.
    cleared = apply_changes(draft, reset)
    assert "k_grid" not in cleared.overrides
    assert "k_grid" not in compute_request(cleared, CAPS)["overrides"]


def test_settings_are_checked_against_the_task_the_same_call_sets() -> None:
    q_grid = next(s for s in CAPS["settings"] if s["key"] == "q_grid")
    assert (
        f"q_grid doesn't apply to the scf_single_point task (it applies to "
        f"{', '.join(q_grid['tasks'])})"
    ) in _refusal({"q_grid": [4, 4, 4]})
    changes = _set({"task": "phonon", "q_grid": [4, 4, 4]})
    applied = apply_changes(_draft(), changes)
    assert (applied.task, applied.overrides) == ("phonon", {"q_grid": [4, 4, 4]})


def test_every_wrong_value_is_refused_at_once_with_the_closest_keys() -> None:
    error = _refusal(
        {
            "walltime": 24,
            "smearing_type": "marzari",
            "k_grid": [8, 8],
            "nstep": 10,
            "use_vdw": "yes",
            "needs_soc": True,
        }
    )
    assert error.startswith("Nothing was set: ")
    assert '"walltime" isn\'t a setting (closest: walltime_h' in error
    assert "smearing_type must be one of cold, fd, gaussian, mp" in error
    assert "k_grid must be a list of 3 integers" in error
    assert "nstep doesn't apply to the scf_single_point task" in error
    assert "use_vdw must be true or false" in error
    assert "needs_soc is advice only" in error


def test_an_unknown_task_or_hpc_profile_is_refused_with_the_ones_core_offers() -> None:
    error = _refusal({"task": "phonons", "hpc": "archer2"})
    assert '"phonons" isn\'t a task goldilocks-core offers: ' in error
    assert "phonon (Phonon (no relaxation))" in error
    assert (
        '"archer2" isn\'t a HPC profile goldilocks-core offers: scarf (scarf)' in error
    )


def test_a_task_the_code_doesnt_run_is_refused() -> None:
    caps = {
        **CAPS,
        "codes": [*CAPS["codes"], {"id": "vasp", "name": "VASP"}],
        "tasks": [
            {**t, "codes": [*t["codes"], "vasp"]}
            if t["id"] == "scf_single_point"
            else t
            for t in CAPS["tasks"]
        ],
    }
    with pytest.raises(ValueError, match="the band task can't run with vasp"):
        setting_changes({"code": "vasp", "task": "band"}, caps, _draft(), INSPECTION)


def test_the_functional_changes_the_way_the_panels_select_does() -> None:
    draft = apply_changes(
        _draft(), _set({"pseudo_table_id": "sssp-pbesol-precision-sr"})
    )
    # A table of another functional than the one set is refused...
    assert (
        "pseudo_table_id must be a table covering this structure for PBE"
        in _refusal(
            {"functional": "PBE", "pseudo_table_id": "sssp-pbesol-precision-sr"}, draft
        )
    )
    # ...and changing the functional alone puts the table back to automatic.
    changes = _set({"functional": "PBE"}, draft)
    assert describe_changes(changes) == {
        "functional": "PBE",
        "pseudo_table_id": "automatic",
    }
    assert apply_changes(draft, changes).overrides == {"functional": "PBE"}
    assert "functional must be one of PBEsol, PBE, LDA" in _refusal(
        {"functional": "B3LYP"}
    )


def test_structure_settings_wait_for_an_open_structure() -> None:
    no_structure = _draft()
    no_structure = no_structure.model_copy(update={"structure": None})
    assert (
        "k_grid describes the structure, so it can only be set once one is open"
        in _refusal({"k_grid": [4, 4, 4], "is_metal": "metal"}, no_structure, None)
    )
    changes = _set({"task": "band", "walltime_h": 12}, no_structure, None)
    assert describe_changes(changes) == {"task": "band", "walltime_h": 12}


def test_what_is_already_set_changes_nothing() -> None:
    draft = _draft(overrides={"walltime_h": 24})
    assert not _set({"walltime_h": 24, "task": "scf_single_point"}, draft)


def test_values_are_asked_for_when_none_are_given() -> None:
    for values in ({}, None, ["task"]):
        assert _refusal(values).startswith("Nothing to set")


def test_the_catalogue_lists_what_the_panel_shows_with_values_and_who_set_them() -> (
    None
):
    draft = _draft(overrides={"walltime_h": 24})
    result = settings_catalogue(CAPS, draft, INSPECTION, EXPLAIN)
    assert result["calculation"]["task"] == {
        "value": "scf_single_point",
        "options": [f"{t['id']} ({t['name']})" for t in CAPS["tasks"]],
    }
    by_key = {entry["key"]: entry for entry in result["settings"]}
    scf_keys = [s["key"] for s in CAPS["settings"] if "scf_single_point" in s["tasks"]]
    overridable_facts = [f["key"] for f in CAPS["facts"] if f["overridable"]]
    assert list(by_key) == [*scf_keys, *overridable_facts]
    assert by_key["k_grid"]["type"] == "list of 3 integers"
    assert by_key["k_grid"]["description"].startswith("Explicit Monkhorst-Pack mesh")
    assert {k: by_key["k_grid"][k] for k in ("group", "value", "source")} == {
        "group": "k_sampling",
        "value": [6, 6, 6],
        "source": "ml",
    }
    assert by_key["smearing_type"]["options"] == ["cold", "fd", "gaussian", "mp"]
    assert {k: by_key["functional"][k] for k in ("value", "source", "options")} == {
        "value": "PBEsol",
        "source": "heuristic",
        "options": ["PBEsol", "PBE", "LDA"],
    }
    assert by_key["walltime_h"]["unit"] == "hours"
    assert by_key["walltime_h"]["set_by_user"] is True
    assert "set_by_user" not in by_key["nodes"]
    assert {k: by_key["is_metal"][k] for k in ("group", "options", "value")} == {
        "group": "analysis",
        "options": ["metal", "non_metal"],
        "value": "non_metal",
    }
    # Small enough to send on demand.
    assert len(json.dumps(result)) < 12_000


def test_the_catalogue_narrows_to_a_group_and_names_the_groups_there_are() -> None:
    no_structure = _draft().model_copy(update={"structure": None})
    job = settings_catalogue(CAPS, no_structure, None, None, group="job")
    assert [e["key"] for e in job["settings"]] == [
        "partition",
        "nodes",
        "ntasks",
        "walltime_h",
        "account",
    ]
    assert job["note"].startswith("No structure is open")
    with pytest.raises(
        ValueError,
        match=r'^No settings group "jobs" for the scf_single_point task\. '
        r"Groups: .*job",
    ):
        settings_catalogue(CAPS, no_structure, None, None, group="jobs")
