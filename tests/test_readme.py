"""The README: what both GHCR package pages show, and where the web build's
Settings sends visitors who want the local version."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _github_anchor(heading: str) -> str:
    """The anchor GitHub gives a Markdown heading: lower case, punctuation
    other than hyphens dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_the_local_version_link_points_at_a_readme_heading() -> None:
    """The web build's "Get the local version" link names a README section;
    rewriting the README must not leave it pointing at nothing."""
    app = (ROOT / "app" / "src" / "App.tsx").read_text()
    url = re.search(r'const LOCAL_VERSION_URL = "([^"]+)"', app).group(1)
    page, _, anchor = url.partition("#")
    assert page == "https://github.com/stfc/goldilocks-agent"
    readme = re.sub(r"```.*?```", "", (ROOT / "README.md").read_text(), flags=re.S)
    headings = re.findall(r"^#+ (.+)$", readme, flags=re.M)
    assert anchor in {_github_anchor(heading) for heading in headings}
