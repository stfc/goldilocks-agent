# Runs inside the NEW agent image (README.md "Upgrading goldilocks-core"):
# installs the pinned goldilocks-core from PyPI into uv's cache and its assets
# into assets-next, without touching what the live container uses, then
# starts a throwaway core on :18000 and smoke-tests it.
set -eu
A="$HOME/.local/share/goldilocks"
# The agent's own requirement string, so this builds the very `uvx`
# environment core_server.py's PyPI mode looks up in the cache.
REQ=$(/app/.venv/bin/python -c 'from goldilocks_agent.core_server import CORE_PYPI_REQUIREMENT as r; print(r)')
VERSION=$(/app/.venv/bin/python -c 'from goldilocks_agent.core_server import CORE_PIN as p; print(p["version"])')
echo "== $REQ"
echo "== copy the live assets to assets-next (the live directory is only read)"
rm -rf "$A/assets-next"
cp -a "$A/assets" "$A/assets-next"
echo "== the release into uv's cache, its assets into assets-next (PSDI downloads; can take many minutes)"
uvx --from "$REQ" goldilocks assets install all
echo "== throwaway core on :18000"
uvx --from "$REQ" goldilocks serve http --host 127.0.0.1 --port 18000 &
pid=$!
ok=0
for i in $(seq 1 180); do
    if curl -sf -m 2 http://127.0.0.1:18000/health >/dev/null; then ok=1; break; fi
    sleep 1
done
[ "$ok" = 1 ] || { echo "core did not answer on :18000"; kill "$pid"; exit 1; }
python3 -I /smoke.py http://127.0.0.1:18000 "$VERSION"
kill "$pid"
echo "== PREWARM OK"
