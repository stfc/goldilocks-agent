# syntax=docker/dockerfile:1

# --- frontend build ---------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /app/app
# `vendor/` too, not just package.json/lock: package.json's goldilocks-workbench
# dependency is a `file:vendor/goldilocks-workbench-*.tgz` -- `npm ci` needs
# it to already exist, not just be promised by package.json (reproduced
# directly 2026-09-25: `npm error enoent ... open '/app/app/vendor/
# goldilocks-workbench-0.0.4.tgz'` without this). Still copied ahead of the
# full `app/` tree below so this layer only invalidates when deps actually
# change, not on every source edit.
COPY app/package.json app/package-lock.json* ./
COPY app/vendor/ ./vendor/
RUN npm ci
COPY app/ ./
# Overridden by deploy/stfc-cloud/docker-compose.yml (`build:stfc-cloud`,
# Vite's `--mode stfc-cloud` reading `app/.env.stfc-cloud`), which bakes in
# the shared-deployment UI flag (design doc §19.1/§19.2). Default here is the
# plain local/desktop build.
ARG FRONTEND_BUILD_SCRIPT=build
RUN npm run ${FRONTEND_BUILD_SCRIPT}

# --- runtime ------------------------------------------------------------
# Ships uv + Python 3.12 preinstalled (matches pyproject.toml's
# requires-python = ">=3.12"). `uv` stays in the final image (not discarded
# after this build) because MLIP Playground (`uv run --project <path> ...`)
# and DFT Workbench (`uvx --from goldilocks-core[http]==<pin> ...`, PyPI mode
# in core_server.py) both shell out to it at runtime.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
WORKDIR /app

# None of `git`/`curl`/`build-essential`/`gfortran` are in the base slim
# image -- needed by this stage's own steps below or at *runtime*, by the
# first `uvx` install of goldilocks-core (core_server.py's
# `_spawn_command()`), long after this build finishes:
# - `git`: fetches enumlib below.
# - `curl`: the STFC Cloud pre-warm (deploy/stfc-cloud/core-upgrade/) probes
#   a throwaway core with it.
# - `build-essential` (g++/gcc/make): goldilocks-ml's own `dscribe` dependency
#   has no prebuilt wheel for every platform (confirmed missing on
#   linux/arm64, 2026-09-25: the install fails with `error: [Errno 2] No
#   such file or directory: 'g++'` compiling dscribe's C++ extensions from
#   source) -- required for DFT Workbench on such platforms.
# - `gfortran`: builds enumlib below (also used to install `libgfortran5`,
#   the shared library enum.x links against at runtime -- apt pulls it in
#   as gfortran's own dependency, no separate line needed).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl build-essential gfortran \
    && rm -rf /var/lib/apt/lists/*

# enumlib (external dependency for AFM magnetic-ordering enumeration --
# see goldilocks-core's advisors/magnetic_config.py, which checks
# `shutil.which("enum.x")` and silently degrades to FM-only, with a
# warning, if it's missing rather than erroring). Built from source the
# same way stfc/goldilocks-core#227 builds it for *that* repo's own
# Dockerfile -- this repo has no enumlib-providing package of its own
# either (Fortran, not pip-installable). pymatgen, in core's own
# environment, runs `makeStr.py` as a program, so its shebang names a Python
# that has numpy, its one dependency: this image's own `/app/.venv` (numpy
# via ase), as goldilocks-core's image names its venv. Core's `uvx`
# environment sits in uv's cache under a hashed path, so it can't be named.
# Pinned to the commit goldilocks-core's own image and CI build (core 0.3.2,
# its ENUMLIB_COMMIT), so both images list orderings with the same code.
ARG ENUMLIB_COMMIT=67217859c1cfe6e1d8cfe25b76a1d14a28c6030e
RUN git clone --quiet https://github.com/msg-byu/enumlib.git /tmp/enumlib \
    && git -C /tmp/enumlib checkout --quiet "$ENUMLIB_COMMIT" \
    && git -C /tmp/enumlib submodule update --quiet --init --recursive \
    && make -C /tmp/enumlib/symlib/src F90=gfortran \
    && make -C /tmp/enumlib/src F90=gfortran \
    && make -C /tmp/enumlib/src F90=gfortran enum.x \
    && install /tmp/enumlib/src/enum.x /usr/local/bin/enum.x \
    && sed '1s|.*|#!/app/.venv/bin/python3|' /tmp/enumlib/aux_src/makeStr.py > /usr/local/bin/makeStr.py \
    && chmod +x /usr/local/bin/makeStr.py \
    && rm -rf /tmp/enumlib

# goldilocks-core itself isn't in the image: core_server.py's PyPI mode
# installs the release core_pin.json names with `uvx` on first use, into
# uv's cache under $HOME (a volume in both compose files), and starts it from
# there. Until core 0.3.0, mMACE needed a fork of mace installed from git, so
# the image baked in core's source and ran it in checkout mode; since then
# core's mMACE dependencies come from PyPI (design doc §19.8). A developer
# checkout still works: set GOLDILOCKS_CORE_PATH and mount it
# (docker-compose.yml).

# `mlip-cli/` must stay a sibling of `src/` -- config.py's mlip_cli_path()
# resolves it relative to the installed package's own file location, not cwd.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY mlip-cli/ ./mlip-cli/

RUN uv sync --locked --no-group dev

COPY --from=frontend-build /app/app/dist ./app/dist

# Pre-create the mount point for the mlip_cli_venv volume (compose service
# `agent`) with the right ownership before it exists -- Docker seeds a fresh
# named volume from whatever's already at that path in the image, so an
# empty dir owned by `goldilocks` here means the later lazy `uv sync` writes
# (janus-core[mace] for MLIP Playground) aren't blocked by a root-owned
# auto-created mount point.
RUN useradd --create-home --home-dir /home/goldilocks goldilocks \
    && mkdir -p /app/mlip-cli/.venv \
    && chown -R goldilocks:goldilocks /app
USER goldilocks
ENV HOME=/home/goldilocks \
    GOLDILOCKS_AGENT_STATIC_DIR=/app/app/dist

EXPOSE 8080

# --no-sync: the venv is already correct from the build-time `uv sync`
# above -- without this, `uv run`'s own implicit sync-check re-resolves
# against pyproject.toml's default groups and silently reinstalls the dev
# group (ruff/pytest/...) on every container start, which both defeats the
# point of `--no-group dev` and needs network access at startup.
CMD ["uv", "run", "--no-sync", "uvicorn", "goldilocks_agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
