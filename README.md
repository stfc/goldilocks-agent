# Goldilocks agent

A chat assistant for setting up DFT calculations. Give it a crystal
structure, or ask it to find one in Materials Project, Materials Cloud, NOMAD
or JARVIS. It shows the Quantum ESPRESSO parameters
[goldilocks-core](https://github.com/stfc/goldilocks-core) recommends, says
where each one comes from, and gives you the input files.

## Use it in your browser

Open **<https://workbench.goldilocks.ac.uk>**. There is nothing to install.

The website's chat model is Qwen3.8-27B, running on STFC's server. It can't
use Claude, OpenAI or Gemini, doesn't search Materials Project and has no
MLIP Playground: those are in the local version below. Your chats and
uploaded structures are kept on STFC's server and deleted 24 hours after your
last message, so don't upload confidential data.

## Run it on your own computer

<!-- --8<-- [start:local-install] -->

The local version is the complete Goldilocks:

- **Chat** with Claude, OpenAI or Gemini using your own API key, or with
  Qwen3.8-27B running on your own computer.
- **Find in Databases** searches Materials Project (with your own free key),
  Materials Cloud, NOMAD and JARVIS.
- **DFT Workbench**, with goldilocks-core running on your computer.
- **MLIP Playground**: MACE single-point energies, geometry optimisation,
  equation of state, NEB and phonons.
- **Your chats stay on your computer** until you delete them.

The steps below take you from nothing to all of that. Do them in order. Each
command is given in full, so you can copy and paste it.

### What you need

- A Mac, a Windows 10 or 11 PC, or a Linux computer, on which you are
  allowed to install programs.
- At least 8 GB of memory.
- About 20 GB of free disk space, and 17 GB more if you run the language
  model on your own computer.
- An internet connection. Each part is downloaded once, the first time you
  use it.

### Opening a terminal

A few steps ask you to run a command in a terminal:

- **Mac**: press ⌘ Space, type `Terminal` and press Return.
- **Windows**: right-click the Start button and choose **Terminal** (on
  older Windows 10, **Windows PowerShell**).
- **Linux**: open your Terminal app (often Ctrl+Alt+T).

Paste the command and press Enter (Return on a Mac) to run it.

## Step 1. Install Docker

Goldilocks runs in Docker, which comes with everything Goldilocks needs. You
install Docker once.

### On a Mac

Docker Desktop supports the current macOS and the two before it.

1. Download Docker Desktop from
    <https://docs.docker.com/desktop/setup/install/mac-install/>. Pick
    **Apple silicon** for a Mac with an M-series chip, **Intel chip** for an
    older one (Apple menu → About This Mac shows which you have).
2. Open the downloaded `Docker.dmg` and drag Docker into Applications.
3. Open Docker from Applications and accept the terms. You don't need to sign
    in or create an account.
4. Wait until Docker Desktop says the engine is running.

### On Windows

You need Windows 10 (version 22H2) or Windows 11 (version 23H2) or newer.

1. Install WSL, which Docker Desktop runs on: right-click the Start button,
    choose **Terminal (Admin)** (on Windows 10, **Windows PowerShell
    (Admin)**), and run

    ```powershell
    wsl --install --no-distribution
    ```

    Restart the computer if it asks you to. If WSL is already installed, run
    `wsl --update` instead.

2. Download Docker Desktop from
    <https://docs.docker.com/desktop/setup/install/windows-install/> and run
    the installer. If it asks which backend to use, choose **WSL 2**.
3. Open Docker Desktop from the Start menu (it doesn't start by itself after
    installing) and accept the terms. You don't need to sign in.
4. Wait until Docker Desktop says the engine is running.

### On Linux

1. Install Docker Engine by following the page for your distribution at
    <https://docs.docker.com/engine/install/>. It installs Docker Compose too.
2. Allow yourself to use Docker without `sudo`:

    ```bash
    sudo usermod -aG docker $USER
    ```

    then log out and log in again.

3. On Fedora, RHEL and CentOS, also start Docker and have it start with the
    computer:

    ```bash
    sudo systemctl enable --now docker
    ```

Docker Desktop is free for personal use, education, non-commercial open
source projects and businesses with fewer than 250 employees and less than
$10 million a year in revenue. Larger organisations and government bodies
need a paid Docker subscription
([Docker's terms](https://docs.docker.com/subscription-billing/desktop-license/)).
If you are at a university or research institute and aren't sure which
applies, ask your IT department. Docker Engine on Linux is free.

### Check Docker works

```bash
docker compose version
```

It should print a version, for example `Docker Compose version v5.5.1`. If
it says the command is not found, or that it cannot connect to the Docker
daemon, Docker isn't installed or isn't running: on a Mac or Windows, open
Docker Desktop and wait for it to start.

## Step 2. Choose a language model

The chat needs a language model. The model explains, asks you questions and
uses Goldilocks' Tools; the DFT parameters always come from goldilocks-core,
whichever model you pick. Choose one, or set up both and switch between them
in the app:

| | A. Cloud model | B. Local model |
|---|---|---|
| Which | Claude, OpenAI or Gemini | Qwen3.8-27B, run by Ollama |
| Runs on | the provider's servers | your own computer |
| You need | an API key, paid for by you | a powerful computer (see B) |
| Your messages and structures | are sent to that provider | stay on your computer |

### A. Cloud model: get an API key

1. Create an account with one provider and make an API key there:
    - Claude: <https://console.anthropic.com/>, then **API keys**
    - OpenAI: <https://platform.openai.com/api-keys>
    - Gemini: <https://aistudio.google.com/apikey>
2. Copy the key and keep it somewhere safe: providers usually show it only
    once. You paste it into Goldilocks in [Step 4](#step-4-open-goldilocks-and-set-it-up).

These are paid services: the provider charges your account for what you use,
and may ask for a payment method or credit before the key works. Check its
prices and what it does with your data.

### B. Local model: install Ollama and Qwen3.8-27B

You need one of:

- a Mac with Apple silicon (M1 or later) and at least 32 GB of memory, or
- a PC whose NVIDIA graphics card has at least 24 GB of memory.

The model uses 16 GB of memory while it runs. On a computer without one of
these it still works, but a reply can take minutes: use a cloud model
instead.

Install Ollama on the computer itself, not inside Docker: Docker can't use a
Mac's graphics chip, so a model inside it replies many times slower.

1. Install Ollama by running, in a terminal:
    - **Mac or Linux**:

        ```bash
        curl -fsSL https://ollama.com/install.sh | sh
        ```

        It may ask for your password. On a Mac, Ollama needs macOS 14 (Sonoma)
        or newer.

    - **Windows**:

        ```powershell
        irm https://ollama.com/install.ps1 | iex
        ```

    Ollama then runs in the background. You can also download an installer
    from <https://ollama.com/download>.

2. Download the model. It is 17 GB, so this can take a while:

    ```bash
    ollama pull qwen3.8
    ```

3. Check it is there:

    ```bash
    ollama list
    ```

    The list should include `qwen3.8:latest`.

4. **Linux only.** Ollama accepts connections only from the computer
    itself, and Goldilocks' Docker container counts as another computer. Make
    Ollama accept them by running

    ```bash
    sudo systemctl edit ollama.service
    ```

    and adding these two lines where the editor says to, then saving and
    closing it:

    ```ini
    [Service]
    Environment="OLLAMA_HOST=0.0.0.0:11434"
    ```

    Then restart Ollama:

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl restart ollama
    ```

    Ollama then also accepts connections from other computers on your
    network, and it has no password: anyone who can reach port 11434 can use
    it. Block that port to other computers in your firewall, or, if you are
    not sure how, use a cloud model instead.

Ollama has to be running while you use the local model. If you quit it,
open it again (Mac, Windows) or run `sudo systemctl start ollama` (Linux).

## Step 3. Download and start Goldilocks

1. Make a folder for Goldilocks and download its one file,
    `docker-compose.yml`, into it. There is no code to download.

    On a Mac or Linux:

    ```bash
    mkdir goldilocks
    cd goldilocks
    curl -O https://raw.githubusercontent.com/stfc/goldilocks-agent/main/docker-compose.yml
    ```

    On Windows, in a normal Terminal (not the Admin one from Step 1):

    ```powershell
    mkdir goldilocks
    cd goldilocks
    curl.exe -O https://raw.githubusercontent.com/stfc/goldilocks-agent/main/docker-compose.yml
    ```

    The folder is in your home folder, for example `/Users/<you>/goldilocks`
    or `C:\Users\<you>\goldilocks`. Commands that update, stop or remove
    Goldilocks are run in it: in a new terminal, type `cd goldilocks` first.

2. Start Goldilocks:

    ```bash
    docker compose up -d
    ```

    The first time, this downloads Goldilocks (about 1 GB). It ends with
    `Container goldilocks-agent-1 Started`.

3. Check it is running:

    ```bash
    docker compose ps
    ```

    `goldilocks-agent-1` should show `Up`.

Goldilocks now runs in the background, and starts again by itself whenever
Docker starts, until you stop it. Docker Desktop (Mac, Windows) doesn't start
by itself when you turn on your computer: open it yourself, or turn on
**Start Docker Desktop when you sign in to your computer** in its
**Settings → General**.

## Step 4. Open Goldilocks and set it up

1. Open <http://localhost:8080> in your web browser. Only your own computer
    can open it.
2. Goldilocks asks how familiar you are with computational materials work.
    This only changes how much it explains; you can change it later in
    **Settings**.
3. **Cloud model:** click **Settings** at the bottom left. Under **Chat model
    and API keys**, paste your key next to Claude, OpenAI or Gemini and click
    **Save**: it shows "✓ Configured". Close Settings.
4. In the model menu next to the message box, pick your model: **Claude**,
    **OpenAI** or **Gemini** for a cloud model, **Qwen3.8-27B** for the local
    one. The menu starts on Claude, so for the local model switch it to
    Qwen3.8-27B. Goldilocks remembers your choice.
5. **Materials Project (optional, free):** log in at
    <https://materialsproject.org/api>, copy your API key from that page and
    paste it in **Settings** under **Databases**. Without it, Find in
    Databases searches the other three databases.

Keys you save stay on your computer, in Goldilocks' Docker storage, and each
is only ever sent to its own provider. You can also put them in a file
instead, see [Configuration](https://stfc.github.io/goldilocks-agent/configuration/).

## Step 5. Try every part

Try each part once. DFT Workbench and MLIP Playground download what they
need the first time you use them, once; after that they start straight away.

1. **Chat and Find in Databases.** Type `find silicon in the databases` and
    press Enter. The reply lists silicon structures and the databases they
    came from. With the local model, the first reply takes a minute or so
    while the model loads.
2. **DFT Workbench.** In the Tools list on the right, click **DFT
    Workbench**. The first time, it installs goldilocks-core with its models
    and pseudopotentials (about 9 GB) and says it is starting; this takes a
    few minutes. Wait until it shows **Structure Setup**.

    Then drag a structure file (CIF, POSCAR, XYZ, ...) onto the chat, or
    attach it with the paperclip button. To try one, save this
    [silicon CIF](https://raw.githubusercontent.com/stfc/goldilocks-agent/main/app/e2e/fixtures/Si.cif)
    with right-click → Save Link As. Type
    `Open this structure in DFT Workbench, review it, and generate the input files.`
    A card shows each recommended parameter and where it comes from; its
    **Open in DFT Workbench** button shows them side by side. The input files
    are under **Files** in the message box.

3. **MLIP Playground.** Close DFT Workbench with the **×** at the top of the
    right-hand panel to get back to the Tools list, and click **MLIP
    Playground**. Your structure is already selected: leave **Singlepoint**
    and click **Run calculation**. The first calculation installs MACE (about
    6 GB, a few minutes); later ones take seconds. The energy appears under
    **Results**.

If all three work, your Goldilocks is complete.
[Tools](https://stfc.github.io/goldilocks-agent/tools/) describes everything
each Tool can do.

## Everyday use

Run these in a terminal, after `cd goldilocks`:

| To | Run |
|---|---|
| Update to the newest version | `docker compose up -d` |
| Stop Goldilocks (your data stays) | `docker compose down` |
| Start it again | `docker compose up -d` |
| See what it is doing | `docker compose logs -f agent` (Ctrl+C stops watching) |
| Remove it and all its data | `docker compose down -v` |

`docker compose up -d` always fetches the newest version first. Your chats,
settings, saved keys and the DFT Workbench and MLIP Playground downloads are
kept in Docker volumes: `down` and `up` keep them, `down -v` deletes them for
good. On a Mac or Windows, Docker Desktop has to be running for Goldilocks
to run (see the end of [Step 3](#step-3-download-and-start-goldilocks)).

## If something goes wrong

**`docker` is not found, or "Cannot connect to the Docker daemon".** Docker
isn't installed or isn't running. On a Mac or Windows, open Docker Desktop
and wait for it to start. On Linux, run `sudo systemctl start docker`; if it
says "permission denied", log out and in again after the `usermod` command
in Step 1.

**"Port is already allocated" when starting.** Something else uses port
8080. Open `docker-compose.yml` in a text editor, change
`"127.0.0.1:8080:8080"` to `"127.0.0.1:8081:8080"`, run
`docker compose up -d` again and open <http://localhost:8081>.

**The chat replies with an authentication error**, such as "Missing
Anthropic API Key". The model picked in the menu has no key, or a wrong one.
The menu starts on Claude, so this is common right after installing: pick
the model you set up, or add the key in **Settings → Chat model and API
keys**.

**No reply from the local model.**

- Check Ollama is running and has the model: `ollama list` should show
  `qwen3.8:latest`.
- Check the model menu says Qwen3.8-27B.
- On Linux, check you did step 4 of [B](#b-local-model-install-ollama-and-qwen38-27b).
- If the reply stops and Ollama's log says the process was killed, the
  computer ran out of memory: close other programs, or use a cloud model.

**DFT Workbench keeps saying it is starting.** The first start downloads
several GB. Follow it with `docker compose logs -f agent`; goldilocks-core's
lines start with `[goldilocks-core]`.

**`docker compose up` is denied `ghcr.io/stfc/goldilocks-agent`.** Your
`docker-compose.yml` is from before 2026-11-11, when the image was renamed.
Download it again ([Step 3](#step-3-download-and-start-goldilocks)).

Something else? [Open an issue](https://github.com/stfc/goldilocks-agent/issues).

<!-- --8<-- [end:local-install] -->

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

- [Documentation](https://stfc.github.io/goldilocks-agent/): these steps,
  configuration, what each Tool does
- [Development](https://github.com/stfc/goldilocks-agent/blob/main/docs/development.md):
  running from source, tests, how the code is laid out
- [Running the website](https://github.com/stfc/goldilocks-agent/blob/main/deploy/stfc-cloud/README.md)
  on STFC Cloud
- [Report a problem or ask a question](https://github.com/stfc/goldilocks-agent/issues)

Developed by [Scientific Computing, UKRI-STFC](https://www.sc.stfc.ac.uk/).
BSD 3-Clause licence.
