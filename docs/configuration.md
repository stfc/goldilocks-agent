# Configuration

Everything here is optional: the compose file from
[Getting started](getting-started.md) already turns on every Tool.

- **API keys** go in the app's **Settings** panel, or in a file named `.env`
  next to `docker-compose.yml`.
- **Other options** go in `docker-compose.yml` itself, under the `agent`
  service's `environment:`.

Run `docker compose up -d` after changing either file.

## API keys

| Key for | In Settings | In `.env` |
|---|---|---|
| Anthropic (Claude) | Chat model and API keys | `ANTHROPIC_API_KEY` |
| OpenAI | Chat model and API keys | `OPENAI_API_KEY` |
| Google (Gemini) | Chat model and API keys | `GEMINI_API_KEY` |
| Materials Project search | Databases | `MP_API_KEY` |

A key in `.env` takes precedence over one saved in Settings. Settings saves
keys inside the container, in `~/.config/goldilocks/config.toml`, which is
kept in the `goldilocks_home` volume. A key is only ever sent to the service
it belongs to.

A Materials Project key is free from
[materialsproject.org/api](https://materialsproject.org/api). Without one,
Find in Databases searches the other databases and skips Materials Project.

## The model

Pick the model in the menu next to the message box. Cloud models need their
API key; the local model, Qwen3.8-27B, needs [Ollama](https://ollama.com)
running on your computer with `ollama pull qwen3.8` done (see
[Getting started](getting-started.md#a-local-model)).

To use one model for every chat whatever the menu says, add it under
`environment:` in `docker-compose.yml` as a
[litellm model name](https://docs.litellm.ai/docs/providers):

```yaml
      GOLDILOCKS_AGENT_MODEL: anthropic/claude-sonnet-5
```

Ollama is expected on your computer at its default port, 11434. The compose
file reaches it as `http://host.docker.internal:11434` (`OLLAMA_API_BASE`).

## Options set in the compose file

`docker-compose.yml` sets these for you:

| Option | Set to | What it does |
|---|---|---|
| `GOLDILOCKS_AGENT_CORE_AUTOSTART` | `1` | Turns on DFT Workbench: Goldilocks installs goldilocks-core from PyPI the first time it is needed and starts it. |
| `UV_TORCH_BACKEND` | `cpu` | Installs goldilocks-core's PyTorch for the CPU, which is smaller and faster there than PyPI's default GPU build. |
| `GOLDILOCKS_AGENT_MLIP_ENABLED` | `1` | Turns on MLIP Playground's calculations. The first one installs its MACE environment, a few GB. |

The DFT parameters themselves always come from goldilocks-core, which has its
own [documentation](https://github.com/stfc/goldilocks-core).

## Running the website

The shared website at [workbench.goldilocks.ac.uk](https://workbench.goldilocks.ac.uk)
uses the `goldilocks-agent-web` image and options of its own (anonymous
visitors, chat retention, a pinned model). They are described in
[`deploy/stfc-cloud/README.md`](https://github.com/stfc/goldilocks-agent/blob/main/deploy/stfc-cloud/README.md).
