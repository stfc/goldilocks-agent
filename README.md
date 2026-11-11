# Goldilocks agent

A chat assistant for setting up DFT calculations. Give it a crystal
structure, or ask it to find one in Materials Project, Materials Cloud, NOMAD
or JARVIS. It shows the Quantum ESPRESSO parameters
[goldilocks-core](https://github.com/stfc/goldilocks-core) recommends, says
where each one comes from, and gives you the input files.

## Use it in your browser

Open **<https://workbench.goldilocks.ac.uk>**. There is nothing to install.
The website searches Materials Cloud, NOMAD and JARVIS; Materials Project is
searched by the local version, with your own free API key.

Your chats and uploaded structures are kept on STFC's server and deleted 24
hours after your last message, so don't upload confidential data. To keep
everything on your own computer, run it there instead.

## Run it on your own computer

You need [Docker Desktop](https://docs.docker.com/get-docker/) (Mac or
Windows) or Docker Engine with Compose (Linux).

1. Download the compose file into a new folder:

   ```bash
   mkdir goldilocks && cd goldilocks
   curl -O https://raw.githubusercontent.com/stfc/goldilocks-agent/main/docker-compose.yml
   ```

2. Start Goldilocks:

   ```bash
   docker compose up -d
   ```

   The first start downloads about 1 GB.

3. Open **<http://localhost:8080>** and choose a model (below).

Goldilocks keeps running in the background, also after you restart your
computer, until you stop it. Your chats, settings and downloads are kept in
Docker volumes, so stopping and starting again loses nothing.

### Choose a model

Goldilocks needs a language model to chat. Pick one:

- **A cloud model** is the quickest. Get an API key from
  [Anthropic](https://console.anthropic.com/),
  [OpenAI](https://platform.openai.com/api-keys) or
  [Google](https://aistudio.google.com/apikey), and paste it into the app
  under **Settings**. Or, before starting, put it in a file named `.env` next
  to `docker-compose.yml`:

  ```bash
  ANTHROPIC_API_KEY=<your key>
  ```

  `OPENAI_API_KEY` and `GEMINI_API_KEY` work the same way. Your messages go
  to that provider.

- **A local model** keeps everything on your computer. Install
  [Ollama](https://ollama.com) on the computer itself, not in Docker, then
  run `ollama pull qwen3.8` and pick **Qwen3.8-27B** in the model menu. The
  model is a 16 GB download and needs about as much free memory.

### Update, stop, remove

Run these in the folder with `docker-compose.yml`:

| To | Run |
|---|---|
| Update to the newest version | `docker compose up -d` |
| See what it's doing | `docker compose logs -f agent` |
| Stop it (your data stays) | `docker compose down` |
| Remove it and all its data | `docker compose down -v` |

The [Getting started guide](https://stfc.github.io/goldilocks-agent/getting-started/)
goes through the first steps in more detail and covers common problems.

## The two Docker images

| Image | What it is |
|---|---|
| [`ghcr.io/stfc/goldilocks-agent-local`](https://github.com/stfc/goldilocks-agent/pkgs/container/goldilocks-agent-local) | Goldilocks for your own computer, the image the steps above run. Keeps your chat history, has every Tool, uses your own model keys. For Intel/AMD and ARM (Apple silicon) machines. |
| [`ghcr.io/stfc/goldilocks-agent-web`](https://github.com/stfc/goldilocks-agent/pkgs/container/goldilocks-agent-web) | The server behind [workbench.goldilocks.ac.uk](https://workbench.goldilocks.ac.uk). You don't need it: use the website, or the local image. |

Both are rebuilt from `main` on every change and tagged `latest` and
`sha-<commit>`. Before 2026-11-11 the local image was called
`ghcr.io/stfc/goldilocks-agent`. That name is no longer available, so replace
a `docker-compose.yml` you downloaded before then.

## More

- [Documentation](https://stfc.github.io/goldilocks-agent/): getting started,
  configuration, what each Tool does
- [Development](https://github.com/stfc/goldilocks-agent/blob/main/docs/development.md):
  running from source, tests, how the code is laid out
- [Running the website](https://github.com/stfc/goldilocks-agent/blob/main/deploy/stfc-cloud/README.md)
  on STFC Cloud
- [Report a problem or ask a question](https://github.com/stfc/goldilocks-agent/issues)

Developed by [Scientific Computing, UKRI-STFC](https://www.sc.stfc.ac.uk/).
BSD 3-Clause licence.
