"""Every place that names a goldilocks-core version must agree with
`src/goldilocks_agent/core_pin.json` (`core_server.CORE_PIN`): the
Dockerfile's fetch, PyPI mode's requirement, and the vendored
goldilocks-workbench tarball, whose `HttpCoreClient` refuses any core that
reports a different `vocabulary_version`. A half-done bump fails here instead
of in the browser. File reads only, no network.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import tarfile
import tomllib
from pathlib import Path

from goldilocks_agent import core_server

REPO_ROOT = Path(__file__).resolve().parents[1]
PIN = core_server.CORE_PIN
TARBALL = REPO_ROOT / "app" / "vendor" / PIN["workbench"]["tarball"]


def _bundle_js() -> str:
    with tarfile.open(TARBALL) as tar:
        member = tar.extractfile("package/dist-lib/index.js")
        assert member is not None
        return member.read().decode("utf-8")


def test_pin_names_an_stfc_release() -> None:
    # PyPI mode installs `version`, and PyPI releases are only ever cut from
    # tags on stfc/goldilocks-core -- so the pin is always one of those.
    assert PIN["repository"] == "https://github.com/stfc/goldilocks-core"
    assert re.fullmatch(r"\d+\.\d+\.\d+", PIN["version"])
    assert PIN["tag"] == f"v{PIN['version']}"
    assert re.fullmatch(r"[0-9a-f]{40}", PIN["commit"])
    assert re.fullmatch(r"\d+", PIN["vocabulary_version"])


def test_pypi_mode_installs_the_pinned_release() -> None:
    requirement = f"goldilocks-core[http]=={PIN['version']}"
    assert requirement == core_server.CORE_PYPI_REQUIREMENT
    # Both the assets install and the serve run the pinned release.
    script = core_server._spawn_command(None)[-1]
    assert script.count(f"--from {shlex.quote(requirement)} ") == 2


def test_dockerfile_fetches_the_pinned_commit_only() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text("utf-8")
    assert "COPY src/goldilocks_agent/core_pin.json" in dockerfile
    # No second, hand-written core ref that could drift from the pin.
    assert "goldilocks-core.git" not in dockerfile
    assert not re.search(r"--branch\s+v\d", dockerfile)


def test_vendored_tarball_is_the_pinned_one() -> None:
    vendored = sorted(path.name for path in TARBALL.parent.glob("*.tgz"))
    assert vendored == [TARBALL.name]
    digest = hashlib.sha256(TARBALL.read_bytes()).hexdigest()
    assert digest == PIN["workbench"]["sha256"]

    spec = f"file:vendor/{TARBALL.name}"
    package = json.loads((REPO_ROOT / "app" / "package.json").read_text("utf-8"))
    assert package["dependencies"]["goldilocks-workbench"] == spec
    lock = json.loads((REPO_ROOT / "app" / "package-lock.json").read_text("utf-8"))
    assert lock["packages"][""]["dependencies"]["goldilocks-workbench"] == spec
    assert lock["packages"]["node_modules/goldilocks-workbench"]["resolved"] == spec


def test_tarball_speaks_the_pinned_vocabulary() -> None:
    # `HttpCoreClient.capabilities()` throws `incompatible_vocabulary` unless
    # `vocabulary_version === <constant>`; the library build keeps that
    # comparison readable enough to recover the constant from the bundle.
    js = _bundle_js()
    guards = re.findall(r"vocabulary_version\s*!==\s*([A-Za-z_$][\w$]*)", js)
    assert len(guards) == 1
    pattern = rf"(?<![\w$]){re.escape(guards[0])}\s*=\s*\"(\d+)\""
    assert re.findall(pattern, js) == [PIN["vocabulary_version"]]


def test_every_route_the_tarball_calls_is_proxied() -> None:
    # A route missing from either list silently breaks just that feature --
    # it happened once with /magnetic-orderings (2026-09-25).
    routes = set(re.findall(r"request\(\s*\"(/[a-z-]+)\"", _bundle_js()))
    assert routes, "no core routes found in the bundle -- did HttpCoreClient change?"
    assert routes <= set(core_server.CORE_PROXIED_PATHS)
    vite_config = (REPO_ROOT / "app" / "vite.config.js").read_text("utf-8")
    dev_proxied = set(
        re.findall(r"'(/[a-z.-]+)':\s*'http://127\.0\.0\.1:8000'", vite_config)
    )
    assert routes <= dev_proxied


def test_core_is_never_a_python_dependency() -> None:
    # Declaring it would make `import goldilocks_core` possible from this
    # environment -- the boundary core#75 was rejected to keep (ruff's TID251
    # ban covers the import side).
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    project = pyproject["project"]
    optional = project.get("optional-dependencies", {}).values()
    groups = pyproject.get("dependency-groups", {}).values()
    declared = [
        *project.get("dependencies", []),
        *(dep for deps in optional for dep in deps),
        *(dep for deps in groups for dep in deps if isinstance(dep, str)),
    ]
    assert not [dep for dep in declared if re.match(r"goldilocks[-_.]core\b", dep)]
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text("utf-8"))
    assert "goldilocks-core" not in {package["name"] for package in lock["package"]}
