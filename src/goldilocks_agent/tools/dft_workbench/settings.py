"""What DFT Workbench's panel lets a user set, for the chat's `dft_settings`
(read) and `dft_set` (change) tools, on the server (#142).

The Calculation form's code, task and HPC profile, and every override
control: one per goldilocks-core setting whose tasks include the draft's
task, plus the overridable facts. Keys are core's own, exactly as the panel
writes them into a draft's overrides (flat, never `steps.<step>.<key>`), so
a value the chat sets is one the panel shows.

Ported from #137's `app/src/dftSettings.ts`, which ran the same rules in the
browser. A refusal raises `ValueError` with every reason at once;
`graph.call_tool` hands its message to the model.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from goldilocks_agent.dft_draft import DftDraft

CALCULATION_FIELDS = ("code", "task", "hpc")

# --- Rules the panel keeps inside its own components -------------------------
# goldilocks-workbench applies these in its Functional and Pseudopotential
# table selects without exporting them; they are mirrored so the chat offers
# and sets what the panel would. Replace them with core's own once it
# exports them (junwen94/goldilocks-core#297).


def functional_options(capabilities: dict) -> list[str]:
    """The functionals the Functional select offers: those of core's tables."""
    return list(
        dict.fromkeys(t["functional"] for t in capabilities["pseudopotential_tables"])
    )


def pseudo_table_options(
    capabilities: dict, inspection: dict | None, functional_override: Any
) -> list[dict]:
    """The tables the Pseudopotential table select offers: those covering
    every element of the structure and, when the functional is overridden,
    of that functional."""
    elements = [] if inspection is None else structure_elements(inspection)
    return [
        table
        for table in capabilities["pseudopotential_tables"]
        if (functional_override is None or table["functional"] == functional_override)
        and all(element in table["elements"] for element in elements)
    ]


def structure_elements(inspection: dict) -> list[str]:
    """The elements of an inspected structure, in order of appearance."""
    symbols = (
        species["symbol"]
        for site in inspection["structure"]["sites"]
        for species in site["species"]
    )
    return list(dict.fromkeys(symbols))


# Changing the functional, or putting it back to automatic, also puts the
# pseudopotential table back to automatic, as the Functional select does.
CLEARED_WITH = {"functional": "pseudo_table_id"}

# --- Reading -------------------------------------------------------------------


def settings_for(capabilities: dict, task: str) -> list[dict]:
    """The settings `task` offers, in core's order."""
    return [s for s in capabilities["settings"] if task in s["tasks"]]


def _resolved_setting(setting: dict, explain: dict | None) -> dict:
    """The value core resolved for `setting`, and the source that decided
    it, as the panel's override control shows them."""
    resolved_in = setting["resolved_in"]
    record = (explain or {}).get("records", {}).get(resolved_in["record"])
    if not record or record.get("status") != "resolved":
        return {}
    value: Any = None
    if resolved_in.get("path") is not None:
        value = record.get("value")
        for key in resolved_in["path"]:
            value = value.get(key) if isinstance(value, dict) else None
    source = (record.get("field_sources") or {}).get(setting["key"]) or record.get(
        "source"
    )
    return {
        **({"value": value} if value is not None else {}),
        **({"source": source} if source else {}),
    }


def _kind_of(setting: dict) -> str:
    """ "list of 3 numbers" for moment_direction, the setting's type otherwise."""
    if not setting.get("items"):
        return setting["type"]
    lo, hi = setting.get("minItems"), setting.get("maxItems")
    size = f"{lo} " if lo is not None and lo == hi else ""
    return f"list of {size}{setting['items']}s"


def _first_sentence(text: str) -> str:
    match = re.search(r"\.(\s|$)", text)
    return text if match is None else text[: match.start() + 1]


def _setting_entry(
    setting: dict,
    capabilities: dict,
    draft: DftDraft,
    inspection: dict | None,
    explain: dict | None,
) -> dict:
    """What the model needs to set `setting`: its key, kind and options, and
    what is set now. `options` is the enum, or for the functional and the
    pseudopotential table the panel's own option lists."""
    if setting["key"] == "functional":
        options = functional_options(capabilities)
    elif setting["key"] == "pseudo_table_id":
        options = [
            f"{t['id']} ({t['provider']} {t['functional']} {t['accuracy']})"
            for t in pseudo_table_options(
                capabilities, inspection, draft.overrides.get("functional")
            )
        ]
    else:
        options = setting.get("enum")
    return {
        "key": setting["key"],
        "group": setting["group"],
        "type": _kind_of(setting),
        **({"options": options} if options else {}),
        **({"unit": setting["unit"]} if setting.get("unit") else {}),
        **_resolved_setting(setting, explain),
        **({"set_by_user": True} if setting["key"] in draft.overrides else {}),
        "description": _first_sentence(setting["description"]),
    }


def _fact_entry(fact: dict, draft: DftDraft, explain: dict | None) -> dict:
    record = (explain or {}).get("records", {}).get(fact["key"])
    return {
        "key": fact["key"],
        "group": "analysis",
        "type": fact["type"],
        **({"options": fact["values"]} if fact.get("values") else {}),
        **(
            {"value": record["value"], "source": record["source"]}
            if record and record.get("status") == "resolved"
            else {}
        ),
        **({"set_by_user": True} if fact["key"] in draft.overrides else {}),
        "description": _first_sentence(fact["description"]),
    }


def groups_for(capabilities: dict, task: str) -> list[str]:
    """The groups `dft_settings` can narrow to for `task`."""
    groups = [s["group"] for s in settings_for(capabilities, task)]
    return list(dict.fromkeys([*groups, "analysis"]))


def _ids(items: list[dict]) -> list[str]:
    return [f"{item['id']} ({item['name']})" for item in items]


def settings_catalogue(
    capabilities: dict,
    draft: DftDraft,
    inspection: dict | None,
    explain: dict | None,
    group: str | None = None,
) -> dict:
    """`dft_settings`'s result: the Calculation form, then every setting the
    panel shows for the draft's task with its current value from `explain`
    (None when no structure is open or core couldn't review it). `group`
    narrows the settings to one group; an unknown group is refused with the
    groups there are."""
    groups = groups_for(capabilities, draft.task)
    if group is not None and group not in groups:
        raise ValueError(
            f"No settings group {json.dumps(group)} for the {draft.task} task. "
            f"Groups: {', '.join(groups)}."
        )
    entries = [
        *(
            _setting_entry(s, capabilities, draft, inspection, explain)
            for s in settings_for(capabilities, draft.task)
        ),
        *(
            _fact_entry(f, draft, explain)
            for f in capabilities["facts"]
            if f.get("overridable")
        ),
    ]
    return {
        "calculation": {
            "code": {"value": draft.code, "options": _ids(capabilities["codes"])},
            "task": {"value": draft.task, "options": _ids(capabilities["tasks"])},
            "hpc": {"value": draft.hpc, "options": _ids(capabilities["hpc_profiles"])},
        },
        **(
            {"note": "No structure is open, so no current values yet."}
            if inspection is None
            else {}
        ),
        "settings": [e for e in entries if group is None or e["group"] == group],
    }


# --- Changing --------------------------------------------------------------------


@dataclass(frozen=True)
class DraftChanges:
    """What a `dft_set` call changes: the calculation fields that change, and
    the overrides that do, None putting one back to automatic."""

    calculation: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.calculation or self.overrides)


def _distance(a: str, b: str) -> int:
    """Edit distance, for "did you mean" suggestions."""
    row = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        previous, row[0] = row[0], i
        for j in range(1, len(b) + 1):
            current = row[j]
            row[j] = min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] != b[j - 1]))
            previous = current
    return row[len(b)]


def _closest(key: str, candidates: list[str]) -> list[str]:
    lower = key.lower()
    scored = sorted(
        candidates,
        key=lambda c: 0 if (c in lower or lower in c) else _distance(lower, c),
    )
    return scored[:3]


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _setting_value_error(
    setting: dict,
    value: Any,
    capabilities: dict,
    inspection: dict | None,
    functional_override: Any,
) -> str | None:
    """Why `value` can't be `setting`'s, or None. Ranges are core's to check."""
    key = setting["key"]
    if key == "functional":
        options = functional_options(capabilities)
        return (
            None
            if value in options
            else f"functional must be one of {', '.join(options)}"
        )
    if key == "pseudo_table_id":
        options = [
            t["id"]
            for t in pseudo_table_options(capabilities, inspection, functional_override)
        ]
        if value in options:
            return None
        scope = "" if functional_override is None else f" for {functional_override}"
        return (
            f"pseudo_table_id must be a table covering this structure{scope}: "
            f"{', '.join(options) or 'none does'}"
        )
    kind = setting["type"]
    if kind == "boolean":
        return None if isinstance(value, bool) else f"{key} must be true or false"
    if kind == "integer":
        return None if _is_integer(value) else f"{key} must be a whole number"
    if kind == "number":
        return None if _is_number(value) else f"{key} must be a number"
    if kind == "string":
        if not isinstance(value, str):
            return f"{key} must be a string"
        enum = setting.get("enum")
        return (
            f"{key} must be one of {', '.join(enum)}"
            if enum and value not in enum
            else None
        )
    if kind == "array":
        lo, hi = setting.get("minItems"), setting.get("maxItems")
        sized = (
            isinstance(value, list)
            and (lo is None or len(value) >= lo)
            and (hi is None or len(value) <= hi)
        )
        check = _is_integer if setting.get("items") == "integer" else _is_number
        typed = isinstance(value, list) and all(check(item) for item in value)
        if sized and typed:
            return None
        expected = _kind_of(setting) if setting.get("items") else "list of numbers"
        return f"{key} must be a {expected}"
    if kind == "object":
        if isinstance(value, dict):
            return None
        return f"{key} must be an object ({_first_sentence(setting['description'])})"
    return None


def _fact_value_error(fact: dict, value: Any) -> str | None:
    if fact["type"] == "boolean":
        return (
            None if isinstance(value, bool) else f"{fact['key']} must be true or false"
        )
    values = fact.get("values") or []
    return (
        None if value in values else f"{fact['key']} must be one of {', '.join(values)}"
    )


def setting_changes(
    values: Any, capabilities: dict, draft: DftDraft, inspection: dict | None
) -> DraftChanges:
    """The draft changes `dft_set`'s `values` ask for -- only what differs
    from the draft, so asking for what is already set sends nothing to core
    again. Raises `ValueError` with why they can't be made, every reason at
    once: a key that isn't a setting of the task (with the closest ones), a
    value of the wrong kind, a structure setting with no structure open.
    Settings are checked against the task the same call sets, if it sets
    one. One bad value refuses them all."""
    if not isinstance(values, dict) or not values:
        raise ValueError(
            "Nothing to set -- give `values`, e.g. "
            '{"task": "band"} or {"walltime_h": 24}.'
        )
    errors: list[str] = []
    offered = {
        "code": ("code", capabilities["codes"]),
        "task": ("task", capabilities["tasks"]),
        "hpc": ("HPC profile", capabilities["hpc_profiles"]),
    }
    for name in CALCULATION_FIELDS:
        if name not in values:
            continue
        what, items = offered[name]
        if not any(item["id"] == values[name] for item in items):
            errors.append(
                f"{json.dumps(values[name])} isn't a {what} goldilocks-core offers: "
                f"{', '.join(_ids(items))}"
            )
    code = values.get("code", draft.code)
    task_id = values.get("task", draft.task)
    task = next((t for t in capabilities["tasks"] if t["id"] == task_id), None)
    if not errors and task and code not in task["codes"]:
        errors.append(
            f"the {task['id']} task can't run with {code} "
            f"(codes that run it: {', '.join(task['codes'])})"
        )
    # The settings are checked against the task, so not against a wrong one.
    if errors:
        raise ValueError(f"Nothing was set: {'; '.join(errors)}.")

    settings = {s["key"]: s for s in capabilities["settings"]}
    facts = {f["key"]: f for f in capabilities["facts"]}
    offered_keys = [
        *CALCULATION_FIELDS,
        *(s["key"] for s in settings_for(capabilities, task_id)),
        *(f["key"] for f in capabilities["facts"] if f.get("overridable")),
    ]
    next_functional = (
        values["functional"]
        if "functional" in values
        else draft.overrides.get("functional")
    )
    overrides: dict[str, Any] = {}
    for key, raw in values.items():
        if key in CALCULATION_FIELDS:
            continue
        setting, fact = settings.get(key), facts.get(key)
        if setting is None and fact is None:
            errors.append(
                f"{json.dumps(key)} isn't a setting (closest: "
                f"{', '.join(_closest(key, offered_keys))}; "
                "dft_settings lists them all)"
            )
            continue
        if fact is not None and not fact.get("overridable"):
            errors.append(
                f"{key} is advice only and can't be set "
                f"({_first_sentence(fact['description'])})"
            )
            continue
        if setting is not None and task_id not in setting["tasks"]:
            errors.append(
                f"{key} doesn't apply to the {task_id} task "
                f"(it applies to {', '.join(setting['tasks'])})"
            )
            continue
        if raw is None:
            if key in draft.overrides:
                overrides[key] = None
            continue
        if (
            fact is not None or setting.get("structure_specific")
        ) and inspection is None:
            errors.append(
                f"{key} describes the structure, so it can only be set once one "
                "is open (open it with dft_open_structure first)"
            )
            continue
        error = (
            _fact_value_error(fact, raw)
            if fact is not None
            else _setting_value_error(
                setting, raw, capabilities, inspection, next_functional
            )
        )
        if error:
            errors.append(error)
        elif raw != draft.overrides.get(key):
            overrides[key] = raw
    for key, cleared in CLEARED_WITH.items():
        if key in overrides and cleared not in values and cleared in draft.overrides:
            overrides[cleared] = None
    if errors:
        raise ValueError(f"Nothing was set: {'; '.join(errors)}.")
    calculation = {
        name: values[name]
        for name in CALCULATION_FIELDS
        if name in values and values[name] != getattr(draft, name)
    }
    return DraftChanges(calculation=calculation, overrides=overrides)


def apply_changes(draft: DftDraft, changes: DraftChanges) -> DftDraft:
    """`draft` with `changes` made, as the panel's `draft.patch` makes them:
    calculation fields replaced, overrides merged, None removing one."""
    overrides = dict(draft.overrides)
    for key, value in changes.overrides.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    return draft.model_copy(update={**changes.calculation, "overrides": overrides})


def describe_changes(changes: DraftChanges) -> dict:
    """What `changes` set, for the model: each key with its new value, or
    "automatic" for one put back."""
    return {
        **changes.calculation,
        **{
            key: "automatic" if value is None else value
            for key, value in changes.overrides.items()
        },
    }
