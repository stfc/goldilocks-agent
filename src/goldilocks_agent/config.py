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


# `core_server.py` reads this to auto-start goldilocks-core's own HTTP
# backend for the embedded DFT Workbench UI. Optional override for someone
# actively developing goldilocks-core itself: if set, `core_server.py` runs
# the checkout's own `uv run --directory <path> poe serve` instead of the
# published PyPI package (see `read_core_autostart_enabled()` below) --
# picks up local, uncommitted changes to core the same session, which the
# published package obviously can't. Most users don't need this at all now
# that goldilocks-core is on PyPI (2026-09-24).
def read_core_path() -> str | None:
    env_value = os.environ.get("GOLDILOCKS_CORE_PATH")
    if env_value:
        return env_value
    return read_config().get("core", {}).get("path")


# Whether `core_server.py` is allowed to auto-start goldilocks-core at all
# when `read_core_path()` is unset -- spawns the real published PyPI
# package (`uvx --from goldilocks-core[http] goldilocks serve http`, no
# checkout needed) rather than doing nothing. Off by default, same
# opt-in-gate reasoning as `read_mlip_enabled()`: a shared/hosted deployment
# must not have its agent server silently reach out to PyPI and spawn a
# whole separate ML-heavy Python process just because network access
# happens to work -- local/desktop users turn this on deliberately.
def read_core_autostart_enabled() -> bool:
    env_value = os.environ.get("GOLDILOCKS_AGENT_CORE_AUTOSTART")
    if env_value is not None:
        return env_value.strip().lower() not in ("", "0", "false", "no")
    return bool(read_config().get("core", {}).get("autostart"))


# mMACE (DFT Workbench's magnetism ML tier -- is_magnetic classification and
# magnetic-ordering ranking) needs a manually-installed, non-PyPI `mace` git
# fork plus a checkpoint file, and only works with checkout mode
# (`read_core_path()` set) -- the PyPI/uvx autostart path has no addressable,
# persistent venv to install the fork into. `core_server.py` reads this to
# decide whether to chain that one-time setup ahead of `poe serve` -- same
# "off unless configured" gate as `read_mlip_enabled()`, except the Docker
# image's own `docker-compose.yml` turns it on by default (its job is "the
# full local experience," same precedent MLIP Playground already set there).
def read_mmace_enabled() -> bool:
    env_value = os.environ.get("GOLDILOCKS_AGENT_MMACE_ENABLED")
    if env_value is not None:
        return env_value.strip().lower() not in ("", "0", "false", "no")
    return bool(read_config().get("core", {}).get("mmace_enabled"))


def mmace_checkpoint_path() -> Path:
    """Where the mMACE checkpoint lives once downloaded -- `$HOME`-relative
    so it survives container restarts via the same volume mechanism
    `~/.cache`/`~/.local/share` already rely on (docker-compose.yml's
    `goldilocks_home`), not something `core_server.py` has to manage itself."""
    return (
        Path.home()
        / ".local/share/goldilocks/mmace/mace_matpes_pbe_baseline_run-3.model"
    )
