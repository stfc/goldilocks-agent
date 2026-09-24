"""User preferences and credentials: ``~/.config/goldilocks/config.toml``.

Design doc 11.2: 0600 permissions, never in git, environment variables
override the file. This is a separate file from ``~/.goldilocks/goldilocks.db``
on purpose -- that db is for scientific artifacts and conversation state
(design doc 16); this file is for things like an API key, which aren't
provenance and shouldn't need a database migration to change.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w

CONFIG_PATH = Path.home() / ".config" / "goldilocks" / "config.toml"

_PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    # Free (not paid) registration key from materialsproject.org -- same
    # storage/precedence rules as the LLM provider keys above, even though
    # it isn't an LLM credential (design doc 11.2 doesn't scope this file to
    # "LLM keys", just "credentials the user provides").
    "materials_project": "MP_API_KEY",
}


def read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return tomllib.loads(CONFIG_PATH.read_text())


def write_credential(provider: str, api_key: str) -> None:
    if provider not in _PROVIDER_ENV_VARS:
        raise ValueError(f"unknown provider: {provider!r}")
    config = read_config()
    config.setdefault("credentials", {})[provider] = api_key
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomli_w.dumps(config))
    CONFIG_PATH.chmod(0o600)


def get_api_key(provider: str) -> str | None:
    """Env var wins over the config file (design doc 11.2: env var is the override)."""
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    return read_config().get("credentials", {}).get(provider)


def configured_providers() -> dict[str, bool]:
    """Which providers have a usable key -- presence only, never the key itself."""
    return {provider: bool(get_api_key(provider)) for provider in _PROVIDER_ENV_VARS}


# Design doc 18/12.2: `experience_level` is a user preference like an API key
# (only affects explanation depth, never a scientific value), so it belongs
# here, not in ~/.goldilocks/goldilocks.db -- it was never actually moved out
# of the frontend's localStorage until now.
_EXPERIENCE_LEVELS = {"new", "familiar", "advanced"}


def read_experience_level() -> str | None:
    return read_config().get("preferences", {}).get("experience_level")


def write_experience_level(level: str) -> None:
    if level not in _EXPERIENCE_LEVELS:
        raise ValueError(f"unknown experience level: {level!r}")
    config = read_config()
    config.setdefault("preferences", {})["experience_level"] = level
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomli_w.dumps(config))
    # Same file a credential might already live in (or be added to later) --
    # keep it 0600 regardless of which kind of write happens to come first.
    CONFIG_PATH.chmod(0o600)


# MLIP Playground (tools/mlip_playground/client.py) shells out to the real
# `janus` CLI (janus-core[mace]) rather than running a persistent HTTP
# service -- superseding the earlier vendored-janus-api/JANUS_API_PATH
# design (2026-09-15). Its isolated environment lives in-repo at
# `./mlip-cli` (checked in, not user-configurable -- there's no external
# checkout to point at the way there is for goldilocks-core), so unlike
# `read_core_path()` this has no path to read. What's still opt-in is
# *whether* it's allowed to run at all: every fresh checkout/test run
# should not silently trigger `uv run --project mlip-cli janus ...`, since
# the first call there pays `uv sync`'s download cost (torch/mace, several
# GB) on top of MACE's own multi-second model load -- same "off unless
# configured" reasoning `read_janus_api_path()` used to document, just
# keyed on a bool now that there's no path left to gate on.
def read_mlip_enabled() -> bool:
    env_value = os.environ.get("GOLDILOCKS_AGENT_MLIP_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() not in ("", "0", "false", "no")
    return bool(read_config().get("mlip", {}).get("enabled"))


def mlip_cli_path() -> Path:
    """`./mlip-cli`, resolved relative to this checkout, not the cwd."""
    return Path(__file__).resolve().parents[2] / "mlip-cli"


# DFT Workbench's subprocess calls (tools/dft_workbench/client.py) shell out
# to `goldilocks` (the CLI, not HTTP/MCP -- see that package's docstring for
# why) inside this checkout. Unlike janus-api, goldilocks-core is *not*
# vendored here -- it's the user's own separate, actively-developed project,
# so this just points at wherever their checkout lives. Not configured =
# clear "core not configured" error, same degradation as
# JANUS_API_PATH/MP_API_KEY.
def read_core_path() -> str | None:
    env_value = os.environ.get("GOLDILOCKS_CORE_PATH")
    if env_value:
        return env_value
    return read_config().get("core", {}).get("path")
