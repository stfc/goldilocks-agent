# Getting started

This is a walkthrough for running goldilocks-agent for the first time. If
you're contributing to the codebase itself, see the main
[`README.md`](../README.md) instead -- it has the same information but
organized for someone who's going to edit the code, not just run it.

Pick one of the two paths below.

> **RAM note (both paths):** the local chat model (`qwen3.8`) is actually a
> 27B-parameter model, not a small one despite the name -- it's ~16GB on
> disk and needs comparable RAM headroom to load. Confirmed the hard way:
> running it under Docker Desktop's default memory allocation gets the
> model's own inference process killed by the kernel (`signal: killed`) as
> soon as it tries to load. If you're on Docker, raise Docker Desktop's
> memory limit (Settings -> Resources -> Memory) to at least ~20GB before
> your first chat message; if you're on Path B (bare-metal Ollama), make
> sure your machine has that much RAM free overall. The app itself starts
> fine regardless -- you'll only hit this once you actually send a chat
> message without enough memory available for the model.

## Path A: Docker (recommended, fewest steps)

Requires [Docker](https://docs.docker.com/get-docker/) (with Compose,
included in current Docker Desktop/Engine installs) **with at least ~20GB of
memory allocated to it** (see the RAM note above). Nothing else -- Python,
Node, and Ollama all run inside containers, you don't install them yourself.

```bash
git clone <this-repo> && cd 1-goldilocks-agent
docker compose up
```

`docker compose up` pulls the `agent` image from `ghcr.io/junwen94/goldilocks-agent`
(built and published automatically on every push to `main` -- see
`.github/workflows/docker-publish.yml`) instead of building it locally, so
this is fast even on a machine with no Python/Node toolchain at all.
**You don't even need the full clone** -- if a colleague just wants to run
it, sending them `docker-compose.yml` on its own (same directory, same
command) is enough; the only thing the full repo adds is the source you'd
need to modify the app itself. `build: .` is kept in the compose file purely
as a local-dev fallback (`docker compose up --build` forces a rebuild from
source instead of pulling).

The first run also pulls the `ollama/ollama` image and downloads the local
chat model (`qwen3.8`, ~16GB) in the background -- this can take a while
depending on your connection. Once it's done, open
<http://localhost:8080>.

What's on by default:
- Chat with the local model, and **Find in Databases** (Materials
  Project/Materials Cloud/NOMAD/JARVIS search).
- **MLIP Playground** (local MACE calculations via `janus-core`) is enabled,
  but the *first* real calculation you run still triggers its own one-time,
  multi-GB download inside the container (installing `janus-core[mace]`) --
  this is independent of the Ollama model download above and only happens
  when you actually use it.
- **DFT Workbench** is enabled too -- goldilocks-core is
  [on PyPI](https://pypi.org/project/goldilocks-core/) now, so no checkout
  needed: the first time you open the Tool, goldilocks-agent auto-starts it
  for you inside the container via `uvx` (needs outbound network to
  pypi.org that first time, otherwise nothing to set up).

What needs extra setup:
- If you're actively developing goldilocks-core itself and want your own
  uncommitted changes reflected, edit `docker-compose.yml`: uncomment the
  `agent` service's `GOLDILOCKS_CORE_PATH` environment variable and the
  matching volume mount under `volumes:`, pointing them at your checkout
  (this takes priority over the PyPI package above), then
  `docker compose up` again.
- Cloud model providers (Claude/OpenAI/Gemini) and Materials Project search
  need API keys. Uncomment and fill in the matching environment variable in
  `docker-compose.yml`'s `agent` service (see the
  [Configuration](../README.md#configuration) table in the README for which
  variable goes with which provider), or set them later from the app's own
  Settings panel once it's running.

Your chat history, saved credentials, and downloaded datasets/models persist
across `docker compose down`/`up` (they live in named Docker volumes, not
inside the container) -- you won't lose anything by restarting.

## Path B: manual local install (bring your own Ollama)

Requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and Node.js
18+/npm on your own machine.

1. **Install Ollama** from [ollama.com](https://ollama.com), then pull the
   model the chat engine uses:
   ```bash
   ollama pull qwen3.8
   ```
2. **Clone and install dependencies:**
   ```bash
   git clone <this-repo> && cd 1-goldilocks-agent
   uv sync --group dev        # backend: Python deps into ./.venv
   cd app && npm install      # frontend: JS deps into app/node_modules
   cd ..
   ```
3. **Start the backend** (in one terminal):
   ```bash
   uv run poe serve            # http://127.0.0.1:8080
   ```
4. **Start the frontend** (in another terminal):
   ```bash
   cd app && npm run dev       # http://localhost:5173
   ```
5. Open <http://localhost:5173>.

That's enough for chat and **Find in Databases**. **MLIP Playground**, **DFT
Workbench**, and cloud model providers are all opt-in extras -- see the
README's [Configuration](../README.md#configuration) section for the
specific environment variables each one needs.
