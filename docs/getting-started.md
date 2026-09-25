# Getting started

This is a walkthrough for running goldilocks-agent for the first time. If
you're contributing to the codebase itself, see the main
[`README.md`](../README.md) instead -- it has the same information but
organized for someone who's going to edit the code, not just run it.

Pick one of the two paths below.

> **Ollama runs natively, not in a container, on both paths.** 2026-09-25:
> confirmed live that Docker Desktop for Mac gives containers no GPU/Metal
> passthrough, so a containerized Ollama runs `qwen3.8` (see the RAM note
> below -- it's a real 27B-parameter model) on pure CPU inference alone --
> measured a single chat reply taking ~3 minutes, and long enough that the
> request eventually timed out mid-stream rather than ever completing.
> Install Ollama on your own machine ([ollama.com](https://ollama.com),
> then `ollama pull qwen3.8`) for *either* path -- Path A's `agent`
> container reaches it via `host.docker.internal`, the same model running
> with real GPU/Metal acceleration instead.

> **RAM note (both paths):** `qwen3.8` is actually a 27B-parameter model,
> not a small one despite the name -- it's ~16GB on disk and needs
> comparable RAM headroom to load, or its own inference process gets killed
> by the kernel (`signal: killed`) as soon as it tries to load. Make sure
> your machine has that much RAM free overall before your first chat
> message. The app itself starts fine regardless -- you'll only hit this
> once you actually send a chat message without enough memory available.

## Path A: Docker (recommended, fewest steps)

Requires [Docker](https://docs.docker.com/get-docker/) (with Compose,
included in current Docker Desktop/Engine installs) and a native
[Ollama](https://ollama.com) install (see the note above -- this is the one
thing Path A doesn't containerize). Python and Node still run entirely
inside the container, nothing to install for those.

```bash
ollama pull qwen3.8
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

Once Ollama has `qwen3.8` pulled (same one-time ~16GB download either way),
open <http://localhost:8080>.

What's on by default:
- Chat with the local model, and **Find in Databases** (Materials
  Project/Materials Cloud/NOMAD/JARVIS search).
- **MLIP Playground** (local MACE calculations via `janus-core`) is enabled,
  but the *first* real calculation you run still triggers its own one-time,
  multi-GB download inside the container (installing `janus-core[mace]`) --
  this is independent of the Ollama model download above and only happens
  when you actually use it.
- **DFT Workbench** is enabled too -- the image bakes in goldilocks-core's
  own source (a pinned release), so the first time you open the Tool,
  goldilocks-agent auto-starts it for you inside the container; that first
  open triggers its own one-time download of goldilocks-core's own
  dependencies, independent of Ollama/MLIP Playground's above.
- **Magnetism ML tier (mMACE)** is enabled too -- `is_magnetic`
  classification and magnetic-ordering ranking in DFT Workbench run on a
  real ML model, not the heuristic/LLM fallback. Piggybacks on DFT
  Workbench's first-open download above with its own extra one-time cost
  (a checkpoint plus a few manually-pinned packages) -- several GB total,
  cached afterward the same way.
- **AFM magnetic-ordering enumeration** works too -- the image builds
  `enum.x`/`makeStr.py` (enumlib) from source, the same way
  [stfc/goldilocks-core#227](https://github.com/stfc/goldilocks-core/pull/227)
  does for that repo's own image. Without these, goldilocks-core doesn't
  error -- it silently degrades to listing only the FM ordering, with a
  warning -- so this is easy to miss if it's ever *not* working.

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

## Path B: manual local install

Requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and Node.js
18+/npm on your own machine (Ollama too -- same native install as Path A
above, not containerized on either path).

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
