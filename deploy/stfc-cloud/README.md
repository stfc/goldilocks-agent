# STFC Cloud deployment

Shared, multi-user, anonymous (no account system) deployment of goldilocks-agent
under `goldilocks.stfc.ac.uk/agent/` -- see `docs/goldilocks-agent-design.md`
§19 for the full design rationale. This is a different deployment from the
repo-root `docker-compose.yml`, which is the single-local-user build.

## Scope of this directory

The agent app + Ollama (serving `qwen3.8:27b` on this VM's own GPU). It does
**not** include nginx or TLS -- `/agent/` is one path segment under a domain
shared with two other repos' own content (`/` the portal, `/workbench`
goldilocks-core/web), so the actual internet-facing nginx + certbot setup is
a domain-wide concern owned by whoever administers that shared entry point,
not this repo. v1's own STFC deployment (`old-goldilcoks-webapp`) used
exactly this split: the backend only ever bound to `127.0.0.1`, and the
domain-wide nginx config lived in the portal repo's own deploy folder.

- `docker-compose.yml` -- the agent container + `ollama` (GPU-backed) +
  a one-shot `ollama-pull` service, agent bound to `127.0.0.1:8080` only.
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
   projects/history panel in the UI, and `GET /agent/api/projects` returns
   `404` (confirms `GOLDILOCKS_AGENT_SHARED_DEPLOYMENT=1` is really set).

## Redeploying

```bash
cd deploy/stfc-cloud
git -C ../.. pull
docker compose up -d --build
```
