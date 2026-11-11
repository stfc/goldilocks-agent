# STFC Cloud deployment: workbench.goldilocks.ac.uk

The shared, anonymous (no account system) deployment of goldilocks-agent, for
people on the STFC network. Design rationale: `docs/goldilocks-agent-design.md`
§19. The repo-root `docker-compose.yml` is the single-user local build, a
different thing.

## Topology (checked 2026-11-10)

| What | Where |
|---|---|
| Instance | `goldilocks-workbench` in OpenStack project "Goldilocks" (openstack.stfc.ac.uk); flavor `g-a100-80gb.x1` (one A100 80 GB); image `goldilocks-agent-gpu-20261002` |
| Addresses | private 192.168.100.226, floating 130.246.214.189 |
| DNS | `workbench.goldilocks.ac.uk` is a CNAME to `host-130-246-214-189.nubes.stfc.ac.uk` |
| Web server | nginx 1.18 (Ubuntu package, a host service, not in Docker) terminates TLS with a Let's Encrypt certificate for both names and proxies everything to `127.0.0.1:8080`. The app is served at `/`. |
| Containers | this directory's `docker-compose.yml`: `agent` (the web build plus goldilocks-core from PyPI at `core_pin.json`'s version, run by `uvx`, on `127.0.0.1:8080`), `ollama` (qwen3.8:27b on the GPU), `ollama-pull` (one-shot model pull), `vllm` (behind a profile, not used) |
| Checkout | `/home/ubuntu/goldilocks-agent`, `origin` = https://github.com/stfc/goldilocks-agent.git, on `main` |

## Access policy

OpenStack security groups enforce it, not nginx. The site has been public
since 2026-11-11; SSH stays STFC-only.

| Security group | Rule |
|---|---|
| `HTTP` | 80/tcp from 0.0.0.0/0. nginx answers with a redirect to HTTPS, and certbot renews through it. |
| `HTTPS` | 443/tcp from 0.0.0.0/0: the site, open to the internet. |
| `goldilocks-stfc-internal` | 22/tcp and 443/tcp from 130.246.0.0/16, 193.62.112.0/24 (Daresbury), 10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16 |

Nothing else is open: the agent listens on loopback. Don't attach `default`:
it opens 22, 8000, 8080, 7777 and ICMP to everyone.

**History.** The site was public briefly on 2026-10-09. Within about an hour,
25 POSTs to `/api/mlip/*` from one address ran the VM out of memory. On
Martin Summers' advice, `HTTPS` was then detached and 443 limited to STFC
ranges. Before reopening:

- MLIP Playground became local-only; its routes 404 here;
- the `agent` container got a 32 GB memory limit;
- mMACE got a 200-atom limit;
- nginx rate-limits chat, `/run`, ranking submissions and core's analysis
  routes (nginx above).

**Back to STFC-only.** In the dashboard, Compute → Instances →
`goldilocks-workbench` → Edit Security Groups, remove `HTTPS`, then Save.
`goldilocks-stfc-internal` still lets STFC ranges in.

**Letting another range reach SSH** (or 443 while STFC-only): Network →
Security Groups → `goldilocks-stfc-internal` → Manage Rules → Add Rule,
Custom TCP Rule, port 22 or 443, with the CIDR. Delete the rule to undo it.
The team OneDrive has the step-by-step version with screenshots:
`stfc cloud deployment/STFC_Cloud_Internal_Access_Goldilocks.docx`.

**Watching for abuse**, in the first days especially:
`sudo tail -f /var/log/nginx/access.log | grep -v '" 200 \|" 304 '` shows
the 429s (rate-limited) and 404s (probes). Answers to `/api/chat` go out as
one long stream, so its lines appear when each reply ends.

## Reaching the VM

From the STFC network or VPN:

```bash
ssh -i ~/.ssh/stfc_goldilocks_core_ed25519 ubuntu@130.246.214.189
```

The key pair is `goldilocks-core` in OpenStack; password login is off.

## nginx

`nginx-workbench.conf` is the enabled site,
`/etc/nginx/sites-available/workbench` on the VM (linked from
`sites-enabled`). It was first copied from the VM byte for byte on
2026-11-10. It:

- proxies `/` to `127.0.0.1:8080`;
- limits `/api/chat` to 10 requests a minute per client IP (burst 5), without
  buffering so replies stream, with a 600 s timeout;
- limits `/api/mlip/` and `/run` to 6 a minute per client IP (burst 3), with
  600 s timeouts;
- limits submitting an mMACE ranking (`POST /magnetic-orderings/rank`) to 6 a
  minute per client IP (burst 3), since core queues ranking jobs without a cap;
  polling or cancelling a job isn't limited;
- limits goldilocks-core's `/explain`, `/inspect` and `/magnetic-orderings` to
  30 a minute per client IP (burst 20);
- carries certbot's TLS lines and its port-80 redirect.

The limits are per client IP. Everyone behind one NAT or VPN exit shares
them, and one chat turn that uses DFT Workbench makes several `/api/chat`
requests: the message, plus one per tool call it hands to the browser. Raise
`rate` and `burst` before a workshop where many people share an address.

To change it, edit the file here, then on the VM:

```bash
sudo cp ~/goldilocks-agent/deploy/stfc-cloud/nginx-workbench.conf /etc/nginx/sites-available/workbench
sudo nginx -t && sudo systemctl reload nginx
```

## TLS

certbot's nginx plugin renews the Let's Encrypt certificate over HTTP-01 on
port 80, from the `certbot.timer` systemd timer, about 30 days before it
expires. The current certificate runs to 2027-01-05. To check:

```bash
systemctl list-timers | grep certbot
sudo certbot certificates
sudo certbot renew --dry-run
```

## Prerequisites on the VM

- `nvidia-container-toolkit` as Docker's runtime: the `ollama` service's GPU
  reservation fails without it.
- **Docker MTU.** The VM's interface has MTU 1450 and Docker's bridges 1500,
  so long downloads from inside a container get reset. Examples are the
  Dockerfile's `git clone`s and core's first `uvx` install. Build with
  `--network host`, and give `--network host` to any one-off container that
  downloads, as the core upgrade below does.

## Redeploying

For a merge that doesn't change `src/goldilocks_agent/core_pin.json`. Run the
web build's end-to-end tests first, locally (`uv run poe e2e-web`,
`app/e2e/README.md`).

```bash
cd ~/goldilocks-agent && git pull --ff-only
cd deploy/stfc-cloud
IMG=goldilocks-agent-stfc-cloud
docker tag $IMG:latest $IMG:previous
docker build --network host --build-arg FRONTEND_BUILD_SCRIPT=build:stfc-cloud -t $IMG:latest ../..
docker compose up -d --no-deps agent
```

- **Why `--no-deps`.** It keeps Ollama running. Without it, compose also
  recreates `ollama` whenever its settings changed, and chat is down while
  the model reloads. When Ollama's settings do change, recreate it on its own
  with `docker compose up -d --no-deps ollama`.
- **Rollback.** `docker tag $IMG:previous $IMG:latest && docker compose up -d --no-deps agent`.
- **A refused `git pull`.** Checkouts from before 2026-11-10 carry a local
  `127.0.0.1:8080:8080` edit to `docker-compose.yml`, which is now committed.
  If `git pull` refuses, run `git checkout -- docker-compose.yml` first.

## Upgrading goldilocks-core

When a merge changes `core_pin.json`, the agent needs the new release's
environment and assets:

- **The environment.** `uvx` installs `goldilocks-core[http]==<version>` from
  PyPI into uv's cache, `~/.cache/uv` in the volume
  `stfc-cloud_goldilocks_home`. Each release keeps its own environment there,
  so the previous one is still there for a rollback.
- **The assets.** They are in `~/.local/share/goldilocks/assets`, in the same
  volume.

Prepare both next to the live ones, then switch, so the site keeps serving
until the switch. The live container must not do the install itself: its
downloads go through Docker's bridge, which resets long ones (Prerequisites).
The scripts are in `core-upgrade/`. Pick a suffix for the old version's
backups, such as `v031`.

**Phase A: no downtime.**

```bash
cd ~/goldilocks-agent && git pull --ff-only && cd deploy/stfc-cloud
IMG=goldilocks-agent-stfc-cloud; HOME_VOL=stfc-cloud_goldilocks_home; OLD=v031
docker tag $IMG:latest $IMG:$OLD
docker build --network host --build-arg FRONTEND_BUILD_SCRIPT=build:stfc-cloud -t $IMG:latest ../..
docker run --rm --network host -v $HOME_VOL:/home/goldilocks \
  -v $PWD/core-upgrade/prewarm.sh:/prewarm.sh:ro -v $PWD/core-upgrade/smoke.py:/smoke.py:ro \
  -e GOLDILOCKS_ASSET_ROOT=/home/goldilocks/.local/share/goldilocks/assets-next \
  -e UV_TORCH_BACKEND=cpu \
  $IMG:latest sh /prewarm.sh
```

The prewarm does four things:

- copies the assets to `assets-next`;
- installs the release from PyPI into uv's cache, with the requirement string
  the agent itself uses and the same `UV_TORCH_BACKEND` as
  `docker-compose.yml`, so the live container finds that environment there;
- installs the release's assets into `assets-next`;
- smoke-tests a throwaway core on :18000.

Stop if it doesn't end with `== PREWARM OK`. Nothing live has changed.

**Phase B: DFT Workbench is down 2–5 minutes, chat a few seconds.**

```bash
docker compose stop agent
docker run --rm --user root -v $HOME_VOL:/h $IMG:latest sh -euc \
  "cd /h/.local/share/goldilocks && mv assets assets-$OLD && mv assets-next assets"
docker compose up -d --no-deps agent
while :; do s=$(curl -s -X POST http://127.0.0.1:8080/api/core-server/ensure); echo "$s"; case "$s" in *'"ready"'*|*'"error"'*) break;; esac; sleep 5; done
python3 core-upgrade/smoke.py http://127.0.0.1:8080 <new version>
python3 core-upgrade/rank_smoke.py http://127.0.0.1:8080
```

**Rollback.** The old image names the old release, whose environment is
still in uv's cache.

```bash
docker compose stop agent
docker run --rm --user root -v $HOME_VOL:/h $IMG:$OLD sh -euc \
  "cd /h/.local/share/goldilocks && mv assets assets-failed && mv assets-$OLD assets"
docker tag $IMG:$OLD $IMG:latest
docker compose up -d --no-deps agent
```

**Cleanup**, once the new version has run for a few days: remove
`assets-$OLD` and `docker image rm $IMG:$OLD`. Old releases' environments stay
in uv's cache until pruned:
`docker compose exec agent uv cache prune` drops what nothing references.

**Moving from checkout mode (once, core 0.3.1 to 0.3.2).** Until core 0.3.2
the image carried core's source and ran it from the volume
`stfc-cloud_goldilocks_core_venv`, with `GOLDILOCKS_CORE_PATH` in
`docker-compose.yml` (design doc §19.8). The steps above do this move too.
Two things differ:

- **Rollback.** The old image needs the old compose file as well, so
  before `docker compose up` in the rollback run
  `git checkout 3b19622 -- docker-compose.yml`, the last commit that ran
  checkout mode. Then, when rolling forward again,
  `git checkout -- docker-compose.yml`.
- **Cleanup.** After the few days, also remove the checkout-mode volumes:
  `docker volume rm stfc-cloud_goldilocks_core_venv stfc-cloud_goldilocks_core_venv_prev stfc-cloud_goldilocks_core_venv_v030`.

## Verifying

From the STFC network:

```bash
curl -sI https://workbench.goldilocks.ac.uk/ | head -1     # HTTP/1.1 200 OK
curl -s https://workbench.goldilocks.ac.uk/capabilities | python3 -c 'import json,sys; print(json.load(sys.stdin)["core_version"])'
curl -s https://workbench.goldilocks.ac.uk/api/projects
# {"detail":"Not available in the shared deployment (no account system)."}
curl -s -X POST https://workbench.goldilocks.ac.uk/api/mlip/singlepoint \
  -H 'content-type: application/json' -d '{"structure_content":"x","structure_name":"x.cif"}'
# {"detail":"MLIP Playground is available in the local version only."}
python3 deploy/stfc-cloud/core-upgrade/smoke.py https://workbench.goldilocks.ac.uk <core version>
```

## Concurrency: Ollama slots

The `ollama` service sets `OLLAMA_NUM_PARALLEL: "4"` and
`OLLAMA_CONTEXT_LENGTH: "65536"` (2026-11-09, #86). The aim is to serve four
chats at once instead of queuing them. The trade-off, a choice made instead
of moving to vLLM (#68), is that 4 x 65536 tokens of KV cache cost what the
old single 262144-token slot did, about 16 GB.

**Measured on the VM on 2026-11-10.** Ollama 0.34.4 ignored the setting:
every request used slot 0, and chats queued as before. After
`docker compose pull ollama` brought 0.40.16, four slots serve four chats at
once.

| Chats at once | Last finished, 0.34.4 | Last finished, 0.40.16 | Aggregate, 0.34.4 → 0.40.16 | Each chat while decoding, 0.40.16 |
|---|---|---|---|---|
| 1 | 9.3 s | 9.1 s | 32 → 33 tok/s | 54 tok/s |
| 2 | 18.9 s | 13.5 s | 32 → 45 tok/s | 36-50 tok/s |
| 4 | 38.5 s | 23.7 s | 31 → 51 tok/s | 17-38 tok/s |
| 8 | 76.5 s | 49.0 s | 31 → 49 tok/s | 15-37 tok/s |

What the numbers mean:

- **One chat is as fast as before.** With 0.34.4 every chat decoded at about
  54 tok/s, but one at a time.
- **Concurrent chats share the GPU instead of queuing.** The last of four
  finishes in 24 s instead of 39 s. Each one streams more slowly while the
  others decode.
- **Four slots are about what the GPU gives.** The aggregate rate levels off
  at about 50 tok/s for four and for eight chats. More slots would slow each
  chat further without finishing a burst sooner.
- **GPU memory is about what one 262144-token slot took.** 37.8 of 80 GB, the
  model 100% on the GPU (`ollama ps`).

The image is still `ollama/ollama:latest`, so a later `docker compose pull
ollama` can bring a different version. Measure again after each pull.

**Measuring.** `ollama_concurrency_bench.py` sends 1, 2, 4 and 8 chats at once,
each with the agent's system prompt and about 4k tokens of attached
structure. It prints when the last one finished, the aggregate token rate,
and each chat's rate while decoding. It imports the agent's system prompt, so
run it in the current `agent` container. The image's system `python` has none
of the agent's packages, hence `uv run --no-sync`.

```bash
cd ~/goldilocks-agent/deploy/stfc-cloud
docker compose exec -T agent uv run --no-sync python - < ollama_concurrency_bench.py | tee ~/ollama-bench-before.txt
# change the ollama service (settings, or `docker compose pull ollama` for a newer image), then
# recreate only Ollama; chat is down while the model reloads (10-55 s)
docker compose up -d --no-deps ollama
docker compose exec -T agent uv run --no-sync python - < ollama_concurrency_bench.py | tee ~/ollama-bench-after.txt
docker compose exec ollama ollama --version
docker compose logs ollama | grep -oE "slot +release: id +[0-9]+" | sed -E "s/ +/ /g" | sort | uniq -c    # requests per slot; ids 0-3 when parallel works
nvidia-smi --query-gpu=memory.used --format=csv
```

Parallel slots are working when the 2- and 4-chat "last finished" times drop
well below the 1-chat time times the number of chats.

**Roll back.** Delete the two `OLLAMA_*` lines and run
`docker compose up -d --no-deps ollama`. That restores one slot with the
model's full context.

## vLLM side by side (stfc/goldilocks-agent#68)

Not in use: on 2026-11-09 Junwen chose Ollama's parallel slots (above) over
switching to vLLM. The service stays here, behind its profile, in case bursts
outgrow what Ollama's slots can serve.

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
