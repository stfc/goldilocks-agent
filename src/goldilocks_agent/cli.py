"""Entry point for the ``goldilocks-agent`` command.

Placeholder -- the agent server (九节: local server + browser) has not been
built yet. See docs/goldilocks-agent-design.md for the design.
"""

from __future__ import annotations

import sys


def main() -> None:
    print(
        "goldilocks-agent is not implemented yet.\n"
        "See docs/goldilocks-agent-design.md for the current design.",
        file=sys.stderr,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
