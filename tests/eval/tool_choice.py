"""Real-model tool-choice eval for picking the chat model (#68, phase 1).

Not collected by pytest, and needs the models it names. Each case is a chat
history; the eval asks a model for the next assistant step the way
`graph.call_llm` does (the agent's own TOOL_SCHEMAS, OpenAI-style messages,
streamed through litellm, no system prompt unless `--system-prompt` is given)
and checks whether that step is the expected one: which tool, with valid
arguments, or no tool. Free-form answers are not judged, except that a
Chinese question gets a Chinese answer and the image case names a number.

Tool results in the multi-step cases are built from goldilocks-core 0.3.0's
recorded responses in app/src/__fixtures__/core, trimmed the way
app/src/dftWorkbench.ts trims them for the model.

    uv run python tests/eval/tool_choice.py \
        "ollama_chat/qwen3.8:latest,reasoning_effort=none,temperature=0.7,top_p=0.8" \
        "ollama_chat/ministral-3:8b,temperature=0.05" --runs 3

Each model argument is a litellm model string followed by comma-separated
key=value completion kwargs. Raw results go to --out as JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import litellm
import openai
from PIL import Image, ImageDraw

from goldilocks_agent.tools import TOOL_SCHEMAS

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "src" / "__fixtures__" / "core"
SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOL_SCHEMAS}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SI_CIF = _fixture("structure")["structure_content"]

NACL_POSCAR = """NaCl
1.0
5.64 0.00 0.00
0.00 5.64 0.00
0.00 0.00 5.64
Na Cl
4 4
Direct
0.0 0.0 0.0
0.5 0.5 0.0
0.5 0.0 0.5
0.0 0.5 0.5
0.5 0.0 0.0
0.0 0.5 0.0
0.0 0.0 0.5
0.5 0.5 0.5
"""

GAAS_CIF = """# generated using pymatgen
data_GaAs
_symmetry_space_group_name_H-M   'P 1'
_cell_length_a   4.06599954
_cell_length_b   4.06599954
_cell_length_c   4.06599954
_cell_angle_alpha   60.00000000
_cell_angle_beta   60.00000000
_cell_angle_gamma   60.00000000
_symmetry_Int_Tables_number   1
_chemical_formula_structural   GaAs
_chemical_formula_sum   'Ga1 As1'
_cell_volume   47.53175000
_cell_formula_units_Z   1
loop_
 _symmetry_equiv_pos_site_id
 _symmetry_equiv_pos_as_xyz
  1  'x, y, z'
loop_
 _atom_site_type_symbol
 _atom_site_label
 _atom_site_symmetry_multiplicity
 _atom_site_fract_x
 _atom_site_fract_y
 _atom_site_fract_z
 _atom_site_occupancy
  Ga  Ga0  1  0.00000000  0.00000000  0.00000000  1
  As  As1  1  0.25000000  0.25000000  0.25000000  1
"""


def _bcc_fe_cif(n: int) -> str:
    """A P1 CIF of an n x n x n bcc Fe supercell (2 n^3 sites), the size of a
    typical database entry for a supercell or a defect cell."""
    a = 2.87 * n
    lines = [
        "data_Fe",
        "_symmetry_space_group_name_H-M   'P 1'",
        *(f"_cell_length_{x}   {a:.8f}" for x in "abc"),
        *(f"_cell_angle_{x}   90.00000000" for x in ("alpha", "beta", "gamma")),
        "_symmetry_Int_Tables_number   1",
        "loop_",
        " _atom_site_type_symbol",
        " _atom_site_label",
        " _atom_site_fract_x",
        " _atom_site_fract_y",
        " _atom_site_fract_z",
        " _atom_site_occupancy",
    ]
    sites = [
        ((i + d) / n, (j + d) / n, (k + d) / n)
        for i in range(n)
        for j in range(n)
        for k in range(n)
        for d in (0.0, 0.5)
    ]
    lines += [
        f"  Fe  Fe{m}  {x:.8f}  {y:.8f}  {z:.8f}  1"
        for m, (x, y, z) in enumerate(sites)
    ]
    return "\n".join(lines) + "\n"


FE128_CIF = _bcc_fe_cif(4)


def _attached(name: str, lang: str, content: str, text: str) -> str:
    """A user message as App.tsx's send() builds it with one attached file."""
    return f"{text}\n\n[Attached file: {name}]\n```{lang}\n{content}```"


def _calculation() -> dict:
    return {
        "code": "quantum_espresso",
        "task": "scf_single_point",
        "hpc": None,
        "overrides": {},
    }


def _open_result() -> dict:
    """dftWorkbench.ts's dft_open_structure result for the Si fixture."""
    s = _fixture("inspect")["structure"]
    lat = s["lattice"]
    return {
        "structure": {
            "formula": s["formula"],
            "reduced_formula": s["reduced_formula"],
            "site_count": s["site_count"],
            "periodicity": s["periodicity"],
            "lattice": {
                k: lat[k]
                for k in ("lengths_angstrom", "angles_degrees", "volume_angstrom3")
            },
            "partially_occupied_sites": 0,
        },
        "calculation": _calculation(),
        "cleared_overrides": [],
        "tasks_available": [t["id"] for t in _fixture("capabilities")["tasks"]],
    }


def _review_result(name: str) -> dict:
    """dftWorkbench.ts's reviewForModel for a recorded /explain response."""
    explain = _fixture(name)
    records = {}
    for key, r in explain["records"].items():
        if r["status"] == "resolved":
            records[key] = {"value": r["value"], "source": r["source"]}
        else:
            records[key] = {"status": r["status"]} | {
                k: r[k] for k in ("reason", "blocked_by") if r.get(k)
            }
    return {
        "calculation": _calculation(),
        "records": records,
        "warnings": [
            {k: w[k] for k in ("record", "level", "message")}
            for w in explain["warnings"]
        ],
        "blocking": [
            {k: b[k] for k in ("summary", "fields", "remedies")}
            for b in explain["blocking"]
        ],
    }


def _blocked_bundle_error() -> str:
    findings = " ".join(
        f"{b['summary']} (resolved by setting "
        f"{' or '.join(json.dumps(r) for r in b['remedies'])})"
        for b in _fixture("explain-blocked")["blocking"]
    )
    return (
        "Input files can't be generated for the current settings: "
        f"{findings} Ask the user before changing any setting."
    )


GAAS_SEARCH = {
    "query_formula": "GaAs",
    "groups": [
        {
            "formula": "GaAs",
            "spacegroup": "F-43m",
            "sources": ["Materials Project", "JARVIS"],
            "entries": [
                {
                    "formula": "GaAs",
                    "spacegroup": "F-43m",
                    "entry_id": "mp-2534",
                    "source": "Materials Project",
                    "url": "https://next-gen.materialsproject.org/materials/mp-2534",
                    "matched": True,
                    "score": None,
                    "band_gap": 0.19,
                    "band_gap_method": "PBE",
                    "energy_above_hull": 0.0,
                },
                {
                    "formula": "GaAs",
                    "spacegroup": "F-43m",
                    "entry_id": "JVASP-1174",
                    "source": "JARVIS",
                    "url": "https://www.ctcms.nist.gov/~knc6/jsmol/JVASP-1174.html",
                    "matched": True,
                    "score": None,
                    "band_gap": 0.75,
                    "band_gap_method": "OptB88vdW",
                },
            ],
            "band_gap": 0.19,
            "band_gap_method": "PBE",
            "energy_above_hull": 0.0,
            "property_sources": {
                "band_gap": "Materials Project",
                "energy_above_hull": "Materials Project",
            },
        },
        {
            "formula": "GaAs",
            "spacegroup": "P6_3mc",
            "sources": ["Materials Project"],
            "entries": [
                {
                    "formula": "GaAs",
                    "spacegroup": "P6_3mc",
                    "entry_id": "mp-8883",
                    "source": "Materials Project",
                    "url": "https://next-gen.materialsproject.org/materials/mp-8883",
                    "matched": True,
                    "score": None,
                    "energy_above_hull": 0.017,
                },
            ],
            "energy_above_hull": 0.017,
            "property_sources": {"energy_above_hull": "Materials Project"},
        },
    ],
    "ungrouped": [],
    "truncated_group_count": 0,
    "unavailable_properties": [],
    "errors": {},
}


def _three_lines_png() -> str:
    """A 1:1 PNG with three coloured lines, as a data URL."""
    img = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(img)
    for i, colour in enumerate(("red", "green", "blue")):
        y = 140 + i * 120
        draw.line([(60, y), (452, y - 80)], fill=colour, width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# --- chat-history builders -------------------------------------------------


def user(content: Any) -> dict:
    return {"role": "user", "content": content}


def call(name: str, args: dict, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def result(call_id: str, output: Any) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(output)}


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


# --- expectations ------------------------------------------------------------


@dataclass
class Step:
    content: str
    tool_calls: list[dict]
    reasoning_chars: int
    seconds: float
    first_token_seconds: float | None
    error: str | None = None


Check = Callable[[Step], str | None]  # None = pass, else why it failed


def expect_tool(
    name: str, args_ok: Callable[[dict], bool] = lambda a: True, why: str = ""
) -> Check:
    def check(step: Step) -> str | None:
        if not step.tool_calls:
            return "no tool call"
        first = step.tool_calls[0]
        if first["name"] != name:
            return f"called {first['name']}"
        try:
            args = json.loads(first["arguments"] or "{}")
        except json.JSONDecodeError:
            return "arguments are not JSON"
        try:
            jsonschema.validate(args, SCHEMAS[name])
        except jsonschema.ValidationError as error:
            return f"arguments fail the schema: {error.message[:80]}"
        return None if args_ok(args) else f"wrong arguments{': ' + why if why else ''}"

    return check


def expect_answer(lang: str | None = None, contains: str | None = None) -> Check:
    def check(step: Step) -> str | None:
        if step.tool_calls:
            return f"called {step.tool_calls[0]['name']}"
        if not step.content.strip():
            return "empty answer"
        if lang == "zh" and not re.search(r"[一-鿿]", step.content):
            return "answer is not in Chinese"
        if contains is not None and contains not in step.content:
            return f"answer lacks {contains!r}"
        return None

    return check


def _names(expected: str) -> Callable[[dict], bool]:
    """Whether dft_open_structure names the chat's file ``expected`` (#73:
    it takes the file's name; the browser reads the file)."""
    return lambda a: a.get("file_name") == expected


def _formula(expected: str) -> Callable[[dict], bool]:
    return lambda a: a.get("formula", "").replace(" ", "") == expected


@dataclass
class Case:
    id: str
    category: str
    messages: list[dict]
    check: Check
    tags: list[str] = field(default_factory=list)


def cases() -> list[Case]:
    si_review = (
        "What parameters would Goldilocks recommend for an SCF calculation "
        "on this structure?"
    )
    si_open_args = {"file_name": "Si.cif"}
    open_turn = [
        user(_attached("Si.cif", "cif", SI_CIF, si_review)),
        call("dft_open_structure", si_open_args, "c1"),
        result("c1", _open_result()),
    ]
    review_turn = [
        *open_turn,
        call("dft_review", {}, "c2"),
        result("c2", _review_result("explain")),
    ]
    gaas_search = [
        user(
            "Find me a starting structure for GaAs in the databases, then set up "
            "an SCF calculation with it."
        ),
        call("find_in_databases", {"formula": "GaAs"}, "s1"),
        result("s1", GAAS_SEARCH),
        assistant(
            "I found two GaAs structures: zinc blende (F-43m, Materials Project "
            "mp-2534 and JARVIS JVASP-1174, on the convex hull) and wurtzite "
            "(P6_3mc, mp-8883, 17 meV/atom above the hull). "
            "Which one should I use?"
        ),
        user("Use the Materials Project zinc blende one, mp-2534."),
    ]
    return [
        # routing: database search
        Case(
            "R1",
            "search",
            [
                user(
                    "Is there already computed data for GaAs in the materials "
                    "databases?"
                )
            ],
            expect_tool("find_in_databases", _formula("GaAs")),
        ),
        Case(
            "R2",
            "search",
            [user("Look up the band gap of TiO2 in the databases.")],
            expect_tool(
                "find_in_databases",
                lambda a: (
                    _formula("TiO2")(a)
                    and "electronic" in a.get("properties", ["electronic"])
                ),
            ),
        ),
        Case(
            "R3",
            "search",
            [user("帮我在材料数据库里查一下 Fe2O3 有没有磁性方面的计算数据。")],
            expect_tool("find_in_databases", _formula("Fe2O3")),
            ["zh"],
        ),
        Case(
            "R4",
            "search",
            [
                user(
                    "I need a starting structure for wurtzite ZnO to run a "
                    "calculation on."
                )
            ],
            expect_tool("find_in_databases", _formula("ZnO")),
        ),
        # no tool
        Case(
            "N1",
            "answer",
            [user("What does smearing do in a DFT calculation of a metal?")],
            expect_answer(),
        ),
        Case(
            "N2",
            "answer",
            [
                user(
                    "I'm writing a paper about NaCl. Can you explain in two "
                    "sentences what a plane-wave cutoff energy is?"
                )
            ],
            expect_answer(),
        ),
        Case(
            "N3",
            "answer",
            [user("你好，你能帮我做什么？")],  # noqa: RUF001
            expect_answer(lang="zh"),
            ["zh"],
        ),
        Case("N4", "answer", [user("Thanks, that's all for now!")], expect_answer()),
        # opening an attached structure
        Case(
            "D1",
            "dft",
            [
                user(
                    _attached(
                        "Si.cif",
                        "cif",
                        SI_CIF,
                        "Set up an SCF calculation for this structure.",
                    )
                )
            ],
            expect_tool(
                "dft_open_structure",
                _names("Si.cif"),
                "not the attached file's name",
            ),
        ),
        Case(
            "D2",
            "dft",
            [
                user(
                    _attached(
                        "POSCAR", "text", NACL_POSCAR, "Open this in the DFT workbench."
                    )
                )
            ],
            expect_tool(
                "dft_open_structure",
                _names("POSCAR"),
                "not the attached file's name",
            ),
        ),
        Case(
            "D3",
            "dft",
            [
                user(
                    _attached(
                        "Si.cif",
                        "cif",
                        SI_CIF,
                        "帮我生成这个结构的 Quantum ESPRESSO 输入文件。",
                    )
                )
            ],
            expect_tool(
                "dft_open_structure",
                _names("Si.cif"),
                "not the attached file's name",
            ),
            ["zh"],
        ),
        Case(
            "D4",
            "dft",
            [
                user(
                    _attached(
                        "Fe_supercell.cif",
                        "cif",
                        FE128_CIF,
                        "Set up an SCF calculation for this supercell.",
                    )
                )
            ],
            expect_tool(
                "dft_open_structure",
                _names("Fe_supercell.cif"),
                "not the attached file's name",
            ),
        ),
        # multi-step
        Case("M1", "multi", open_turn, expect_tool("dft_review")),
        Case("M2", "multi", review_turn, expect_answer()),
        Case(
            "M3",
            "multi",
            [
                *review_turn,
                assistant(
                    "Goldilocks recommends a 48 Ry wavefunction cutoff, 192 Ry for "
                    "the density, and a Monkhorst-Pack k-point grid from its "
                    "k-distance model, with PseudoDojo PBEsol pseudopotentials."
                ),
                user("Great, generate the input files."),
            ],
            expect_tool("dft_download_bundle"),
        ),
        Case(
            "M4",
            "multi",
            [
                *open_turn,
                call("dft_review", {}, "c2"),
                result("c2", _review_result("explain-blocked")),
                assistant(
                    "One setting needs a decision before input files can be "
                    "generated: isolation=slab doesn't suit "
                    "this cell."
                ),
                user("Just generate the input files."),
                call("dft_download_bundle", {}, "c3"),
                {
                    "role": "tool",
                    "tool_call_id": "c3",
                    "content": json.dumps({"error": _blocked_bundle_error()}),
                },
            ],
            expect_answer(),
        ),
        Case(
            "M5",
            "multi",
            gaas_search,
            expect_tool(
                "get_structure",
                lambda a: (
                    a["source"] == "Materials Project" and a["entry_id"] == "mp-2534"
                ),
            ),
        ),
        Case(
            "M6",
            "multi",
            [
                *gaas_search,
                call(
                    "get_structure",
                    {"source": "Materials Project", "entry_id": "mp-2534"},
                    "s2",
                ),
                result(
                    "s2",
                    {
                        "filename": "GaAs_mp-2534.cif",
                        "content": GAAS_CIF,
                        "format": "cif",
                    },
                ),
            ],
            expect_tool(
                "dft_open_structure",
                _names("GaAs_mp-2534.cif"),
                "not the fetched file's name",
            ),
        ),
        # image attached in the composer (App.tsx sends it as an image_url part)
        Case(
            "V1",
            "vision",
            [
                user(
                    [
                        {
                            "type": "text",
                            "text": "How many lines are drawn in this image? "
                            "Reply with only the number.",
                        },
                        {"type": "image_url", "image_url": {"url": _three_lines_png()}},
                    ]
                )
            ],
            expect_answer(contains="3"),
        ),
    ]


# --- running -----------------------------------------------------------------


def parse_model(spec: str) -> tuple[str, dict]:
    model, *pairs = spec.split(",")
    kwargs: dict[str, Any] = {}
    for pair in pairs:
        key, value = pair.split("=", 1)
        try:
            kwargs[key] = float(value) if "." in value else int(value)
        except ValueError:
            kwargs[key] = value
    return model, kwargs


async def next_step(model: str, kwargs: dict, messages: list[dict]) -> Step:
    """One assistant step, accumulated the way graph.call_llm does."""
    start = time.monotonic()
    first: float | None = None
    content, reasoning = "", ""
    calls: dict[int, dict] = {}
    try:
        response = await litellm.acompletion(
            model=model, messages=messages, tools=TOOL_SCHEMAS, stream=True, **kwargs
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            if first is None and (delta.content or delta.tool_calls):
                first = time.monotonic() - start
            content += delta.content or ""
            reasoning += getattr(delta, "reasoning_content", None) or ""
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"name": None, "arguments": ""})
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
    except openai.OpenAIError as error:  # litellm's errors: a failed step, not a crash
        return Step(
            content,
            [],
            len(reasoning),
            time.monotonic() - start,
            first,
            f"{type(error).__name__}: {error}",
        )
    return Step(
        content, list(calls.values()), len(reasoning), time.monotonic() - start, first
    )


async def evaluate(
    spec: str, case_list: list[Case], runs: int, system_prompt: str | None, out
) -> dict:
    model, kwargs = parse_model(spec)
    await next_step(
        model, kwargs, [user("Hi")]
    )  # load the model, so latency excludes it
    rows = []
    for case in case_list:
        messages = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + case.messages
        for run in range(runs):
            step = await next_step(model, kwargs, messages)
            failure = step.error or case.check(step)
            row = {
                "model": spec,
                "case": case.id,
                "category": case.category,
                "run": run,
                "passed": failure is None,
                "failure": failure,
                "seconds": round(step.seconds, 2),
                "first_token_seconds": None
                if step.first_token_seconds is None
                else round(step.first_token_seconds, 2),
                "content_chars": len(step.content),
                "reasoning_chars": step.reasoning_chars,
                "tool_calls": [c["name"] for c in step.tool_calls],
                "arguments": [c["arguments"] for c in step.tool_calls],
                "content": step.content,
            }
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            mark = "ok " if failure is None else "FAIL"
            print(
                f"  {spec[:44]:44s} {case.id} run{run} {mark} "
                f"{row['seconds']:6.1f}s {failure or ''}",
                flush=True,
            )
    return {"spec": spec, "rows": rows}


def report(results: list[dict], case_list: list[Case]) -> None:
    categories = sorted({c.category for c in case_list})
    print("\n== summary (pass rate; seconds median / p90; answer chars median)")
    header = (
        f"{'model':46s} {'all':>6s} "
        + " ".join(f"{c:>7s}" for c in categories)
        + "   s_med   s_p90  chars"
    )
    print(header)
    for res in results:
        rows = res["rows"]

        def rate(rs):
            return (
                f"{100 * sum(r['passed'] for r in rs) / len(rs):5.0f}%"
                if rs
                else "    -"
            )

        secs = sorted(r["seconds"] for r in rows)
        chars = statistics.median(r["content_chars"] for r in rows)
        per_cat = " ".join(
            f"{rate([r for r in rows if r['category'] == c]):>7s}" for c in categories
        )
        print(
            f"{res['spec'][:46]:46s} {rate(rows):>6s} {per_cat}   "
            f"{statistics.median(secs):5.1f}   "
            f"{secs[int(0.9 * (len(secs) - 1))]:5.1f}  {chars:5.0f}"
        )
    print("\n== per case (passes / runs)")
    print(
        f"{'case':6s}"
        + "".join(f"{res['spec'].split('/')[-1][:22]:>24s}" for res in results)
    )
    for case in case_list:
        cells = []
        for res in results:
            rs = [r for r in res["rows"] if r["case"] == case.id]
            fails = sorted({r["failure"] for r in rs if r["failure"]})
            cells.append(
                f"{sum(r['passed'] for r in rs)}/{len(rs)} "
                f"{(fails[0] if fails else '')[:16]}"
            )
        print(f"{case.id:6s}" + "".join(f"{c:>24s}" for c in cells))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "models",
        nargs="*",
        help="litellm model string, then ,key=value completion kwargs",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument(
        "--system-prompt", help="file whose text is sent as a system message"
    )
    parser.add_argument("--out", type=Path, default=Path("tool_choice_results.jsonl"))
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="run nothing; report every model already in --out",
    )
    args = parser.parse_args()
    case_list = cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        case_list = [c for c in case_list if c.id in wanted]
    if args.report_only:
        by_model: dict[str, list[dict]] = {}
        for line in args.out.read_text().splitlines():
            row = json.loads(line)
            by_model.setdefault(row["model"], []).append(row)
        seen = {row["case"] for rows in by_model.values() for row in rows}
        report(
            [{"spec": spec, "rows": rows} for spec, rows in by_model.items()],
            [c for c in case_list if c.id in seen],
        )
        return
    system_prompt = Path(args.system_prompt).read_text() if args.system_prompt else None
    results = []
    with args.out.open("a") as out:
        for spec in args.models:
            results.append(
                await evaluate(spec, case_list, args.runs, system_prompt, out)
            )
    report(results, case_list)


if __name__ == "__main__":
    asyncio.run(main())
