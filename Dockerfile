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
RUN npm run build

# --- runtime ------------------------------------------------------------
# Ships uv + Python 3.12 preinstalled (matches pyproject.toml's
# requires-python = ">=3.12"). `uv` stays in the final image (not discarded
# after this build) because MLIP Playground and DFT Workbench both shell
# out to `uv run --project <path> ...` at runtime, not just at build time.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
WORKDIR /app

# None of `git`/`curl`/`build-essential`/`gfortran` are in the base slim
# image -- all needed at *runtime*, not just here, by checkout mode's own
# lazy `--extra http` sync (core_server.py's `_spawn_command()`), long
# after this build finishes:
# - `git`: mMACE's fork is a `uv pip install`ed git dependency
#   (GOLDILOCKS_AGENT_MMACE_ENABLED).
# - `curl`: mMACE's checkpoint download.
# - `build-essential` (g++/gcc/make): goldilocks-ml's own `dscribe` dependency
#   has no prebuilt wheel for every platform (confirmed missing on
#   linux/arm64, 2026-09-25: `uv sync --extra http` alone -- nothing to do
#   with mMACE -- fails with `error: [Errno 2] No such file or directory:
#   'g++'` compiling dscribe's C++ extensions from source) -- required for
#   plain checkout-mode DFT Workbench on such platforms, not just mMACE.
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
# either (Fortran, not pip-installable). `makeStr.py`'s shebang points at
# goldilocks-core's own venv (not this image's `/app/.venv`) since that's
# whose pymatgen actually shells out to it -- the path doesn't exist yet
# at build time (that venv is synced lazily at container runtime, see
# `git clone` below), only needs to by the time this script actually runs.
RUN git clone --recursive --depth 1 https://github.com/msg-byu/enumlib.git /tmp/enumlib \
    && make -C /tmp/enumlib/symlib/src F90=gfortran \
    && make -C /tmp/enumlib/src F90=gfortran \
    && make -C /tmp/enumlib/src F90=gfortran enum.x \
    && install /tmp/enumlib/src/enum.x /usr/local/bin/enum.x \
    && sed '1s|.*|#!/opt/goldilocks-core/.venv/bin/python3|' /tmp/enumlib/aux_src/makeStr.py > /usr/local/bin/makeStr.py \
    && chmod +x /usr/local/bin/makeStr.py \
    && rm -rf /tmp/enumlib

# `mlip-cli/` must stay a sibling of `src/` -- config.py's mlip_cli_path()
# resolves it relative to the installed package's own file location, not cwd.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY mlip-cli/ ./mlip-cli/

RUN uv sync --locked --no-group dev

COPY --from=frontend-build /app/app/dist ./app/dist

# goldilocks-core's *source* only (a few MB) -- the default DFT Workbench
# backend (docker-compose.yml's GOLDILOCKS_CORE_PATH) for anyone using this
# image, not just mMACE users, since checkout mode is what mMACE requires
# and is a strict superset of the PyPI/uvx autostart path otherwise. Pinned
# to v0.1.1: verified 2026-09-25 as the newest tag on the real
# stfc/goldilocks-core remote that both matches a real PyPI release
# (pyproject.toml's own version = "0.1.1") and already has full
# magnetism/mMACE support (is_magnetic.py/magnetic_ordering_ml.py present,
# goldilocks-ml==0.2.3 pinned) -- one trivial, frontend-only commit behind
# the v2 branch tip. Its own deps (torch et al.) are deliberately NOT
# installed here -- see core_server.py's lazy `--extra http` sync, same
# "first real use pays the cost" pattern as mlip-cli/.venv below.
RUN git clone --branch v0.1.1 --depth 1 \
    https://github.com/stfc/goldilocks-core.git /opt/goldilocks-core

# Pre-create the mount points for the mlip_cli_venv/goldilocks_core_venv
# volumes (compose service `agent`) with the right ownership before they
# exist -- Docker seeds a fresh named volume from whatever's already at
# that path in the image, so an empty dir owned by `goldilocks` here means
# the later lazy `uv sync`/`uv pip install` writes (janus-core[mace] for
# MLIP Playground, goldilocks-core's own deps plus mMACE's manual packages
# for DFT Workbench) aren't blocked by a root-owned auto-created mount point.
RUN useradd --create-home --home-dir /home/goldilocks goldilocks \
    && mkdir -p /app/mlip-cli/.venv /opt/goldilocks-core/.venv \
    && chown -R goldilocks:goldilocks /app /opt/goldilocks-core
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
