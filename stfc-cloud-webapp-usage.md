# Running Goldilocks Locally with STFC Cloud vLLM

## Architecture

```
Browser
  │  http://localhost:5174
  ▼
Vite dev server (goldilocks-web, port 5174)
  │  /api/* requests → proxied to localhost:8080
  ▼
goldilocks-api (FastAPI, port 8080)
  │  LLM requests → localhost:8000
  ▼
SSH tunnel
  │  localhost:8000 → 172.16.111.119:8000
  ▼
vLLM + Qwen3 (STFC cloud server)
```

**Why the tunnel?**
The STFC server is on an internal network. Even with VPN, you can reach it via SSH — but your local programs (like `goldilocks-api`) talk to `localhost`, not a remote IP. The SSH tunnel creates a local port (8000) that secretly forwards all traffic through SSH to the server's vLLM port.

**Why goldilocks-api?**
The browser can't reach vLLM directly — it doesn't know about the SSH tunnel. `goldilocks-api` sits in the middle: it receives requests from the browser and forwards them to vLLM through the tunnel.

---

## Prerequisites

- VPN connected to STFC network
- vLLM running on the server (see below)
- `uv` installed locally

---

## Workflow (4 terminals)

### Terminal 1 — SSH tunnel

```bash
ssh -i ~/.ssh/id_rsa -L 8000:localhost:8000 ubuntu@172.16.111.119 -N
```

This will hang silently — that's correct. It means the tunnel is up. **Do not close this terminal.**

`-N` means "tunnel only, no shell".

### Terminal 2 — goldilocks-api (backend)

```bash
cd /Users/junwen.yin/research/1-goldilocks/code/goldilocks-api
uv run uvicorn app.main:app --port 8080 --reload
```

### Terminal 3 — goldilocks-web (frontend)

```bash
cd /Users/junwen.yin/research/1-goldilocks/code/goldilocks-web
npm run dev
```

### Terminal 4 — check vLLM on server (if needed)

```bash
ssh -i ~/.ssh/id_rsa ubuntu@172.16.111.119
screen -r vllm
```

vLLM is started with:
```bash
screen -S vllm
vllm serve Qwen/Qwen3-8B --host 0.0.0.0 --port 8000 --dtype bfloat16 --max-model-len 32768 --gpu-memory-utilization 0.85
```

---

## Open the app

Go to `http://localhost:5174` (port may shift to 5175 etc. if 5173 is taken — check terminal output).

---

## Troubleshooting

**`bind [127.0.0.1]:8000: Address already in use`**
Local port 8000 is occupied. Find and kill it:
```bash
lsof -ti:8000 | xargs kill
```
Then re-run the tunnel command.

**`error: Failed to spawn: uvicorn`**
The `.venv` is in a broken state (e.g. created by another tool). Delete it and rebuild:
```bash
rm -rf .venv
uv sync
uv run uvicorn app.main:app --port 8080 --reload
```
