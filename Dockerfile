# syntax=docker/dockerfile:1

# --- frontend build ---------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /app/app
COPY app/package.json app/package-lock.json* ./
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

# `mlip-cli/` must stay a sibling of `src/` -- config.py's mlip_cli_path()
# resolves it relative to the installed package's own file location, not cwd.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY mlip-cli/ ./mlip-cli/

RUN uv sync --locked --no-group dev

COPY --from=frontend-build /app/app/dist ./app/dist

# Pre-create the mount point for the mlip_cli_venv volume (compose service
# `agent`) with the right ownership before it exists -- Docker seeds a
# fresh named volume from whatever's already at that path in the image, so
# an empty dir owned by `goldilocks` here means `uv sync`'s later write (the
# lazy janus-core[mace] install, see mlip_playground/client.py) isn't
# blocked by a root-owned auto-created mount point.
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
