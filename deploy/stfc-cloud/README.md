# STFC Cloud deployment

Shared, multi-user, anonymous (no account system) deployment of goldilocks-agent,
eventually reachable under `goldilocks.stfc.ac.uk/agent/` -- see
`docs/goldilocks-agent-design.md` §19 for the full design rationale. This is a
different deployment from the repo-root `docker-compose.yml`, which is the
single-local-user build.

The frontend has two build variants for this deployment (`app/package.json`):
`build:stfc-cloud-public` (root-relative asset paths swapped for `/agent/`,
`.env.stfc-cloud-public`) for once this sits behind the shared-domain nginx,
and `build:stfc-cloud-internal` (`.env.stfc-cloud-internal`, root-relative
paths kept) for direct access to this VM's own address in the meantime --
`docker-compose.yml`'s `FRONTEND_BUILD_SCRIPT` picks one explicitly; they are
not interchangeable without also changing the `ports` binding below.

## Scope of this directory

The agent app + Ollama (serving `qwen3.8:27b` on this VM's own GPU). It does
**not** include nginx or TLS -- `/agent/` is one path segment under a domain
shared with two other repos' own content (`/` the portal, `/workbench`
goldilocks-core/web), so the actual internet-facing nginx + certbot setup is
a domain-wide concern owned by whoever administers that shared entry point,
not this repo. v1's own STFC deployment (`old-goldilcoks-webapp`) used
exactly this split: the backend only ever bound to `127.0.0.1`, and the
domain-wide nginx config lived in the portal repo's own deploy folder.

- `docker-compose.yml` -- the agent container + `ollama` (GPU-backed) + a
  one-shot `ollama-pull` service. Currently binds `agent` to `8080:8080`
  (reachable from anyone on the STFC internal network/VPN, once the
  OpenStack Security Group and this VM's own `ufw` both allow 8080/tcp) --
  an interim direct-access setup, not the eventual `127.0.0.1:8080:8080` +
  shared-domain-nginx design (see the `ports` comment in that file).
- `nginx-agent.conf` -- the `location /agent/ { ... }` snippet to hand to
  whoever owns the shared nginx config. Not a standalone site file.

## Known external blockers (as of 2026-09-29)

- **STFC floating IP/subdomain**: not allocated yet. Nothing below can be
  verified against the real domain until it lands.
- **STFC intranet/VPN CIDR range**: needed from STFC IT for
  `nginx-agent.conf`'s `allow` line -- not guessable.
- **Real concurrency capacity for Ollama serving `qwen3.8:27b`**: needs
  hands-on load testing once GPU access exists (design doc §19.7 -- Ollama's
  throughput, not the session architecture, is the expected bottleneck).
  Don't assume a safe `GOLDILOCKS_AGENT_CHAT_CONCURRENCY_LIMIT` without
  measuring on the real hardware.
- Requires `nvidia-container-toolkit` configured as the Docker runtime on
  the host before `docker compose up` -- the `ollama` service's GPU
  reservation will fail to start without it.

## Bring-up

1. Build and start the agent + Ollama:
   ```bash
   cd deploy/stfc-cloud
   docker compose up -d --build
   curl -fsS http://127.0.0.1:8080/api/credentials   # sanity check
   ```
2. Fill in `nginx-agent.conf`'s `<STFC_INTRANET_CIDR>` placeholder and hand
   it to whoever administers the shared `goldilocks.stfc.ac.uk` nginx config
   -- it needs `include`ing inside the `server {}` block that already
   terminates TLS for the whole domain (design doc §19.8: certbot + Let's
   Encrypt, HTTP-01 challenge -- already decided, implemented at that
   domain-wide level, not here).
3. Once DNS/TLS land end-to-end, verify streaming actually works through the
   full chain (nginx's `proxy_buffering off` is required for this, already
   set in `nginx-agent.conf`):
   ```bash
   curl -N -X POST https://goldilocks.stfc.ac.uk/agent/api/chat \
     -H 'content-type: application/json' \
     -d '{"thread_id":"smoke-test","message":{"role":"user","content":"Hello"}}'
   ```
4. Confirm the shared-deployment gating actually took effect: no sidebar
   projects/history panel in the UI, and the backend's own refusal on the
   VM itself -- match the `detail` text, not just a 404 status: the backend
   serves no `/agent/`-prefixed routes, so a bare `/agent/api/...` request
   lands on the static frontend's own 404/405 whatever the gating.
   ```bash
   curl -s http://127.0.0.1:8080/api/projects
   # {"detail":"Not available in the shared deployment (no account system)."}
   ```
   MLIP Playground and Beyond DFT are *Coming soon* this release: they
   appear only in the full-page All Tools view, unopenable, and the MLIP
   routes refuse even a well-formed request (a malformed one gets FastAPI's
   own 422 first):
   ```bash
   curl -s -X POST http://127.0.0.1:8080/api/mlip/singlepoint \
     -H 'content-type: application/json' \
     -d '{"structure_content":"x","structure_name":"x.cif"}'
   # {"detail":"MLIP Playground is not available in this release."}
   ```
   MLIP Playground is also off at this deployment's own layer
   (`docker-compose.yml`'s commented-out
   `GOLDILOCKS_AGENT_MLIP_ENABLED`/`mlip_cli_venv`).

## vLLM side by side (stfc/goldilocks-agent#68)

The `vllm` service serves the same chat model as Ollama (qwen3.8 27B, thinking
off) for a load test, while Ollama keeps serving production. It sits behind the
`vllm` compose profile, so `docker compose up -d` never starts it, and the agent
doesn't use it until the cutover.

**Choices, 2026-11-09:**
- Checkpoint `RedHatAI/Qwen3.8-27B-INT4`: W4A16, which runs on Marlin kernels on
  the A100, with the vision tower left unquantized. It is the same 4-bit class
  as Ollama's Q4_K_M, and its ~19.5 GB of weights fit beside Ollama's ~36 GB.
  The FP8 checkpoint (~31 GB) would leave only ~5 GB for the KV cache.
- KV cache: only 16 of the model's 64 layers use full attention; the rest are
  linear attention with a fixed-size state. That makes about 64 KB per token,
  so a typical 5k-token chat holds about 330 MB.
- `vllm/vllm-openai:v0.32.0`, the CUDA 13.0 build. The VM's driver (595)
  supports CUDA 13.2.
- Flags from vLLM's Qwen3.8-27B recipe, plus thinking off for every request
  (`--default-chat-template-kwargs`).
- A100 is not among the recipe's verified hardware, so step 2 below is the
  first real check that it loads and answers.

**1. Start it** (the first run downloads ~20 GB of weights):

```bash
cd ~/goldilocks-agent && git pull
cd deploy/stfc-cloud
docker compose --profile vllm up -d vllm   # runs vllm-download first, then vLLM
docker compose logs -f vllm                # wait for "Application startup complete"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
# expect Ollama ~36 GB + vLLM ~38 GB, under 80 GB
```

**2. Smoke test:** a plain answer with no reasoning, then a tool call.

```bash
curl -s http://127.0.0.1:8001/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen3.8-27b", "temperature": 0.7, "top_p": 0.8,
  "messages": [{"role": "user", "content": "In one sentence, what is DFT?"}]
}' | python3 -m json.tool
# choices[0].message.content is filled; reasoning_content is empty or absent

curl -s http://127.0.0.1:8001/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen3.8-27b", "temperature": 0.7, "top_p": 0.8,
  "messages": [{"role": "user", "content": "Find GaAs in the materials databases."}],
  "tools": [{"type": "function", "function": {"name": "find_in_databases",
    "description": "Search materials databases by formula.",
    "parameters": {"type": "object", "properties": {"formula": {"type": "string"}},
                   "required": ["formula"]}}}]
}' | python3 -m json.tool
# choices[0].message.tool_calls[0].function: find_in_databases, {"formula": "GaAs"}
```

**3. Load test** with 1, 2, 4 and 8 concurrent chats. Prompts are 4096 tokens,
the production median, and outputs are 512 tokens. This shares the GPU with
production Ollama, so run it when nobody is chatting.

```bash
for c in 1 2 4 8; do
  docker compose exec vllm vllm bench serve \
    --backend openai-chat --endpoint /v1/chat/completions \
    --base-url http://127.0.0.1:8000 --model qwen3.8-27b \
    --tokenizer RedHatAI/Qwen3.8-27B-INT4 \
    --dataset-name random --random-input-len 4096 --random-output-len 512 --ignore-eos \
    --num-prompts $((c * 8)) --max-concurrency $c \
    --percentile-metrics ttft,tpot,e2el --metric-percentiles 50,95 \
    | tee ~/vllm-bench-c$c.txt
done
```

The gate from #68:
- **One user is at least as fast as Ollama today**: 61 tok/s, so TPOT p50 at
  `c=1` is 16.4 ms or less.
- **4 users don't queue**: TTFT p95 at `c=4` stays within twice that at `c=1`.

Tool-call correctness and image attach run separately, through
`tests/eval/tool_choice.py` over an SSH tunnel to `127.0.0.1:8001`.

**4. Stop it** to free the GPU. The weights stay in the `hf_cache` volume.

```bash
docker compose --profile vllm stop vllm
```

The cutover is not part of this trial. When it happens, the agent gets
`GOLDILOCKS_AGENT_MODEL=hosted_vllm/qwen3.8-27b` and
`VLLM_API_BASE=http://vllm:8000/v1`. `resolve_model()` already gives that model
qwen's non-thinking sampling.

## Redeploying

```bash
cd deploy/stfc-cloud
git -C ../.. pull
docker compose up -d --build
```

## Memory limit

The `agent` service runs with `mem_limit: 32g` and `memswap_limit: 32g`
(stfc/goldilocks-agent#70, 2026-11-09). Swap is not allowed past the limit.

**Why a limit.** Since core v0.3.0 the agent container also runs
goldilocks-core's mMACE processes, and the is_magnetic worker's memory grows
with the structure: about 45 MB per atom, 11.8 GB for a 250-atom cell. Without
a container limit, a large upload can push the VM into a global OOM kill. The
kernel then kills the largest process on the host, which can be Ollama (19 GB
RSS), taking chat down for everyone. Under the container limit, the kernel
kills the largest process inside this container instead, and logs
`Memory cgroup out of memory`.

**Why 32 GB.** Measured on 2026-11-09:

| What | Memory |
|---|---|
| Agent server (uvicorn) | 1.1 GB |
| Idle goldilocks-core (main process + is_magnetic worker + enumlib worker) | about 3.4 GB |
| Whole container while a ranking runs | about 4.4 GB |
| VM RAM | 98 GB, of which Ollama uses about 19 GB |

32 GB is about 7 times the busy steady state. It still fits a structure of
roughly 600 atoms through the is_magnetic worker, and leaves most of the VM's
RAM to Ollama, the OS, and vLLM later (#68). Lower it once
junwen94/goldilocks-core#259 (atom limit) and stfc/goldilocks-ml#140 (no_grad
embedding) are deployed.

**What should happen at the limit.** For an oversized structure, the largest
process in the container is the mMACE worker. Core raises `WorkerDied` for
that request and starts a new worker on the next call. The agent server and
core's main process stay up, and Ollama keeps running in its own container.

Check after a redeploy:

```bash
docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
  "$(docker compose ps -q agent)"
# 34359738368 34359738368
```

Before relying on the kill behaviour, try it once on the VM with an oversized
structure: that request should fail, the next `/explain` should succeed, chat
should keep working, and `docker compose logs agent` / `dmesg` should show the
cgroup OOM kill.
