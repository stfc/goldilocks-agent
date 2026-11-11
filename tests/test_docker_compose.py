"""The repository's docker-compose.yml: the file the README has people
download to run the local version."""

from __future__ import annotations

from pathlib import Path

import yaml

from goldilocks_agent.config import _PROVIDER_ENV_VARS

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_keys_in_a_dot_env_file_reach_the_container() -> None:
    """docs/configuration.md puts keys in a `.env` file next to
    docker-compose.yml.
    Compose only uses that file to fill in `${...}`, so a key reaches the
    container only if `environment` lists it, with no value: it is then
    passed through when set and left unset otherwise."""
    environment = yaml.safe_load(COMPOSE.read_text())["services"]["agent"][
        "environment"
    ]
    for env_var in _PROVIDER_ENV_VARS.values():
        assert env_var in environment, env_var
        assert environment[env_var] is None, env_var
