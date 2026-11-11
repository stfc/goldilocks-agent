# Getting started

This guide runs Goldilocks on your own computer with Docker. If you only want
to try it, the website [workbench.goldilocks.ac.uk](https://workbench.goldilocks.ac.uk)
needs no installation.

## What you need

- [Docker Desktop](https://docs.docker.com/get-docker/) on Mac or Windows,
  or Docker Engine with the Compose plugin on Linux.
- About 1 GB of disk space for Goldilocks itself, and a few GB more the first
  time you open DFT Workbench (see [First steps](#3-first-steps)).
- A language model: an API key for a cloud model, or
  [Ollama](https://ollama.com) with a local model (16 GB, see
  [Choose a model](#2-choose-a-model)).

## 1. Install and start

Make a folder for Goldilocks and download its compose file into it. This one
file is all you need; there is no code to download.

```bash
mkdir goldilocks && cd goldilocks
curl -O https://raw.githubusercontent.com/stfc/goldilocks-agent/main/docker-compose.yml
docker compose up -d
```

The first `docker compose up -d` downloads the Goldilocks image (about 1 GB).
When it finishes, open <http://localhost:8080>. Goldilocks only listens on
your own computer, not on your network.

Goldilocks runs in the background and starts again after you restart your
computer, until you stop it with `docker compose down`.

## 2. Choose a model

Goldilocks needs a language model to chat. The model explains and asks; the
DFT parameters always come from goldilocks-core, whichever model you pick.

### A cloud model

Quickest to set up. Get an API key from
[Anthropic](https://console.anthropic.com/) (Claude),
[OpenAI](https://platform.openai.com/api-keys) or
[Google](https://aistudio.google.com/apikey) (Gemini). Then either:

- paste it into the app under **Settings → Model**, or
- put it in a file named `.env` next to `docker-compose.yml`, then run
  `docker compose up -d` again:

  ```bash
  ANTHROPIC_API_KEY=<your key>
  ```

  `OPENAI_API_KEY` and `GEMINI_API_KEY` work the same way. A key in `.env`
  takes precedence over one saved in Settings.

Pick the provider in the model menu next to the message box. Your messages
and attached structures are sent to that provider.

### A local model

Keeps everything on your computer. Install [Ollama](https://ollama.com) on
the computer itself, not in a Docker container: Docker usually doesn't give
containers your GPU (Docker Desktop on a Mac never does), so a model in a
container replies many times slower. Then download the model:

```bash
ollama pull qwen3.8
```

and pick **Qwen3.8-27B** in the model menu. Despite its name it is a
27-billion-parameter model: a 16 GB download that needs about as much free
memory to run. The first reply after Ollama starts takes longer while the
model loads.

## 3. First steps

1. When you first open Goldilocks, it asks how familiar you are with
   computational materials work. This only changes how much it explains.
2. Drag a structure file (CIF, POSCAR, XYZ, XSF, ...) onto the window, or ask
   Goldilocks to find one, for example "find silicon in the databases".
3. Ask for what you want to calculate, for example "recommend parameters for
   an SCF calculation". A card shows each recommended value and where it
   comes from; **Open in DFT Workbench** shows them all side by side.
4. Ask for the input files, or download them from DFT Workbench.

The first time DFT Workbench is used, Goldilocks installs goldilocks-core
with its models and pseudopotentials: a few GB, which takes some minutes.
DFT Workbench says it is starting while this runs. It only happens once; the
download is kept with your other data.

Searching Materials Project needs a free API key from
[materialsproject.org/api](https://materialsproject.org/api). Add it under
**Settings → Databases**, or as `MP_API_KEY` in `.env`. Without one, the
other databases are searched and Materials Project is skipped.

## Update, stop, remove

Run these in the folder with `docker-compose.yml`:

| To | Run |
|---|---|
| Update to the newest version | `docker compose up -d` (it always fetches the latest image) |
| Follow what it's doing | `docker compose logs -f agent` |
| Stop it; your data stays | `docker compose down` |
| Remove it and all its data | `docker compose down -v` |

Your chats, settings, API keys saved in Settings and the DFT Workbench
download are kept in Docker volumes, so `docker compose down` and `up` lose
nothing. `down -v` deletes them.

## Common problems

**Port 8080 is already in use.** In `docker-compose.yml`, change
`"127.0.0.1:8080:8080"` to another port on the left, for example
`"127.0.0.1:8081:8080"`, run `docker compose up -d` and open
<http://localhost:8081>.

**Chat replies with an authentication error.** The cloud model you picked
has no API key, or a wrong one. In the model menu, a provider with a saved
key says "✓ API key saved". Add or fix the key in **Settings → Model**, or
pick another model.

**No reply from the local model.**

- Check Ollama is running and has the model: `ollama list` should show
  `qwen3.8`.
- On Linux, Ollama only accepts connections from the computer itself by
  default, which a Docker container isn't. Make it listen on all interfaces
  by setting `OLLAMA_HOST=0.0.0.0` for the Ollama service (see Ollama's
  [FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.md)), then
  restart it.
- If the reply stops and Ollama's log says the process was killed, the
  computer ran out of memory: close other programs or use a cloud model.

**DFT Workbench keeps saying it is starting.** The first start downloads a
few GB. Follow it with `docker compose logs -f agent`; goldilocks-core's
lines start with `[goldilocks-core]`.

**`docker compose up` is denied `ghcr.io/stfc/goldilocks-agent`.** Your
`docker-compose.yml` is from before 2026-11-11, when the image was renamed
`goldilocks-agent-local`; the old name is no longer available. Download the
current file (step 1).

Something else? [Open an issue](https://github.com/stfc/goldilocks-agent/issues).

## Without Docker

To run Goldilocks from its source code, for example to change it, see
[Development](development.md).
