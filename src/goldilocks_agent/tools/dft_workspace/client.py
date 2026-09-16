"""Shells out to the `goldilocks` CLI inside a configured goldilocks-core
checkout -- not HTTP/MCP (see package docstring for why: the CLI already
covers inspect/explain/run/capabilities identically, needs no persistent
service/port. The one real gap this used to have -- no CLI equivalent of
HTTP/MCP's one-call `/capabilities` -- was goldilocks-core#62, filed and
closed the same day, 2026-09-15: `capabilities --json` is now a real thin
CLI entry point onto the same function).

Every call writes `structure_content` to a temp file first -- the CLI
takes a file path, not inline content (unlike HTTP's
`InlineStructureDocument`).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from goldilocks_agent.config import read_core_path
from goldilocks_agent.tools.dft_workspace.models import (
    CapabilitiesResult,
    ExplainResult,
    InspectResult,
    ResolvedField,
    RunResult,
)

# `goldilocks run` without `-o` is documented as "memory-only preview" --
# verified live 2026-09-15 that mode is the *only* one returning
# `records`/`warnings`; with `-o` the CLI instead reports `{files: [...],
# kind, path}` about what it wrote to disk. `run()` below always passes
# `-o` (it needs real file content, not a preview), so it never sees
# `records`/`warnings` -- the Explain tab gets those from `explain()`.

_TIMEOUT = 60.0


def _require_core_path() -> Path:
    configured = read_core_path()
    if not configured:
        raise RuntimeError(
            "goldilocks-core not configured -- set GOLDILOCKS_CORE_PATH to "
            "a local goldilocks-core checkout to enable DFT Workspace."
        )
    path = Path(configured)
    if not path.is_dir():
        raise RuntimeError(f"GOLDILOCKS_CORE_PATH does not exist: {path}")
    return path


async def _run_cli(*args: str) -> str:
    core_path = _require_core_path()
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "--project",
        str(core_path),
        "goldilocks",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(
            f"goldilocks {args[0]} timed out after {_TIMEOUT}s"
        ) from None
    if proc.returncode != 0:
        # The CLI already prints clear, specific messages (missing
        # pseudopotential table, bad --set key, etc.) -- surface them
        # verbatim rather than a generic "exit code 1".
        raise RuntimeError(stderr.decode().strip() or f"goldilocks {args[0]} failed")
    return stdout.decode()


def _compute_args(
    code: str | None,
    task: str | None,
    hpc: str | None,
    overrides: dict[str, object] | None,
) -> list[str]:
    args = []
    if code:
        args += ["--code", code]
    if task:
        args += ["--task", task]
    if hpc:
        args += ["--hpc", hpc]
    for key, value in (overrides or {}).items():
        # `--set` expects a valid-JSON value for array/object types (e.g.
        # 'k_grid=[8,8,8]'), a bare value otherwise -- json.dumps handles
        # both (numbers/bools/lists encode correctly; plain strings need
        # no quoting on the CLI side, but quoting them is still valid JSON
        # and goldilocks-core's own --set parser accepts it either way).
        args += ["--set", f"{key}={json.dumps(value)}"]
    return args


async def inspect_structure(
    structure_content: str, structure_name: str
) -> InspectResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        stdout = await _run_cli("inspect", str(struct_path), "--json")
    return InspectResult(**json.loads(stdout))


async def explain(
    structure_content: str,
    structure_name: str,
    code: str | None = None,
    task: str | None = None,
    hpc: str | None = None,
    overrides: dict[str, object] | None = None,
) -> ExplainResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        stdout = await _run_cli(
            "explain",
            str(struct_path),
            "--json",
            *_compute_args(code, task, hpc, overrides),
        )
    data = json.loads(stdout)
    return ExplainResult(
        records={k: ResolvedField(**v) for k, v in data["records"].items()},
        warnings=data["warnings"],
    )


async def run(
    structure_content: str,
    structure_name: str,
    code: str | None = None,
    task: str | None = None,
    hpc: str | None = None,
    overrides: dict[str, object] | None = None,
) -> RunResult:
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        out_dir = Path(tmp) / "out"
        stdout = await _run_cli(
            "run",
            str(struct_path),
            "--json",
            "-o",
            str(out_dir),
            *_compute_args(code, task, hpc, overrides),
        )
        data = json.loads(stdout)
        # UPF is plain text (XML-ish), not binary -- included like every
        # other file (2026-09-15, second pass: the Inputs tab now lists
        # pseudo alongside the input/submission-script files on purpose).
        files = {name: (out_dir / name).read_text() for name in data["files"]}
    return RunResult(files=files)


async def run_bundle(
    structure_content: str,
    structure_name: str,
    code: str | None = None,
    task: str | None = None,
    hpc: str | None = None,
    overrides: dict[str, object] | None = None,
) -> bytes:
    """Same underlying `goldilocks run`, but `-o <path>.zip` instead of a
    directory -- for the Inputs tab's one-click "download bundle" button,
    which wants real archive bytes to hand the browser, not a preview
    dict. A second CLI call rather than reusing `run()`'s directory output
    because the CLI's `-o` only publishes to *one* destination per call
    (directory XOR zip, verified live) -- cheap either way, `run` itself
    takes ~1s."""
    with tempfile.TemporaryDirectory() as tmp:
        struct_path = Path(tmp) / structure_name
        struct_path.write_text(structure_content)
        zip_path = Path(tmp) / "bundle.zip"
        await _run_cli(
            "run",
            str(struct_path),
            "--json",
            "-o",
            str(zip_path),
            *_compute_args(code, task, hpc, overrides),
        )
        return zip_path.read_bytes()


async def capabilities() -> CapabilitiesResult:
    """`goldilocks capabilities --json` -- goldilocks-core#62 (filed and
    closed 2026-09-15): codes/tasks/pseudopotential_tables/hpc_profiles/
    settings/facts/warnings-catalog in one call, the CLI's own thin entry
    point onto the same `capabilities()` function HTTP/MCP already both
    called. Static per core checkout -- callers may want to cache this,
    `server.py` does."""
    stdout = await _run_cli("capabilities", "--json")
    return CapabilitiesResult(**json.loads(stdout))
