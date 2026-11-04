"""Move every goldilocks-core pin to one stfc release tag.

Clones the tag from the pin's repository, builds the goldilocks-workbench
library from that commit, vendors the packed tarball in `app/vendor/`
(replacing the old one), rewrites `src/goldilocks_agent/core_pin.json`, and
reinstalls the tarball so `app/package.json` and its lock name it.
`tests/test_core_pin.py` then checks that every pin site agrees.

Usage: `uv run poe bump-core v0.3.0`. Needs git, uv, and Node >= 24 with npm
on PATH, plus network access to GitHub, PyPI and npm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPO_ROOT / "src" / "goldilocks_agent" / "core_pin.json"
VENDOR_DIR = REPO_ROOT / "app" / "vendor"
MIN_NODE_MAJOR = 24


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    print(f"$ {' '.join(args)}  (in {cwd})", flush=True)
    result = subprocess.run(
        args, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE, text=True
    )
    return result.stdout.strip()


def _clean_env() -> dict[str, str]:
    """This process's environment without the agent's own uv context, which
    would otherwise leak into the uv commands run inside the core clone."""
    env = dict(os.environ)
    for key in ("VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH"):
        env.pop(key, None)
    return env


def _require_node(env: dict[str, str]) -> None:
    version = subprocess.run(
        ["node", "--version"], env=env, check=True, stdout=subprocess.PIPE, text=True
    ).stdout.strip()
    major = int(version.lstrip("v").split(".")[0])
    if major < MIN_NODE_MAJOR:
        sys.exit(
            f"Node >= {MIN_NODE_MAJOR} is required to build goldilocks-workbench "
            f"(found {version}); switch first, for example with `nvm use 24`."
        )


def _core_metadata(core: Path, tag: str) -> tuple[str, str]:
    """The release version and vocabulary version of the clone at `core`.
    Exits unless pyproject, web/package.json and the tag name one version."""
    version = tomllib.loads((core / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
    web_version = json.loads((core / "web" / "package.json").read_text("utf-8"))[
        "version"
    ]
    if tag != f"v{version}" or web_version != version:
        sys.exit(
            f"{tag}: pyproject says {version} and web/package.json says "
            f"{web_version}; a release tag names one version for both."
        )
    capabilities = (core / "src" / "goldilocks_core" / "capabilities.py").read_text(
        "utf-8"
    )
    match = re.search(r'^VOCABULARY_VERSION = "(\d+)"$', capabilities, re.MULTILINE)
    if match is None:
        sys.exit("VOCABULARY_VERSION not found in capabilities.py")
    return version, match.group(1)


def bump(tag: str) -> None:
    pin = json.loads(PIN_PATH.read_text("utf-8"))
    repository = pin["repository"]
    env = _clean_env()
    _require_node(env)
    with tempfile.TemporaryDirectory(prefix="bump-core-") as tmp:
        core = Path(tmp) / "goldilocks-core"
        clone = ["git", "clone", "--quiet", "--depth", "1", "--branch", tag]
        _run([*clone, f"{repository}.git", str(core)], Path(tmp), env)
        commit = _run(["git", "rev-parse", "HEAD"], core, env)
        version, vocabulary = _core_metadata(core, tag)

        # generate:openapi imports goldilocks_core[http] through `uv run`.
        _run(["uv", "sync", "--frozen", "--extra", "http"], core, env)
        web = core / "web"
        _run(["npm", "ci"], web, env)
        _run(["npm", "run", "generate:api"], web, env)
        _run(["npm", "run", "build:lib"], web, env)
        packed = _run(["npm", "pack", "--pack-destination", tmp], web, env)
        tarball_name = packed.splitlines()[-1]
        expected_name = f"goldilocks-workbench-{version}.tgz"
        if tarball_name != expected_name:
            sys.exit(f"npm pack wrote {tarball_name}, expected {expected_name}")

        for old in VENDOR_DIR.glob("*.tgz"):
            old.unlink()
        tarball = VENDOR_DIR / tarball_name
        shutil.copyfile(Path(tmp) / tarball_name, tarball)

    pin.update(
        tag=tag,
        commit=commit,
        version=version,
        vocabulary_version=vocabulary,
        workbench={
            "tarball": tarball_name,
            "sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        },
    )
    PIN_PATH.write_text(json.dumps(pin, indent=2) + "\n", "utf-8")
    _run(["npm", "install", f"./vendor/{tarball_name}"], REPO_ROOT / "app", env)
    print(
        f"Pinned goldilocks-core {tag} ({commit[:8]}, vocabulary {vocabulary}). "
        "Next: uv run poe check && uv run poe web-check."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="stfc/goldilocks-core release tag, e.g. v0.3.0")
    bump(parser.parse_args().tag)


if __name__ == "__main__":
    main()
