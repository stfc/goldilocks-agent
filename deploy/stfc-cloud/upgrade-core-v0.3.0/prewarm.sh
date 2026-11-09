# Runs inside the NEW agent image (see the runbook): prepares goldilocks-core
# v0.3.0's environment and assets without touching what the live container
# uses, then starts a throwaway core on :18000 and smoke-tests it.
set -eu
A="$HOME/.local/share/goldilocks"
echo "== copy the live assets to assets-next (the live directory is only read)"
rm -rf "$A/assets-next"
cp -a "$A/assets" "$A/assets-next"
cd /opt/goldilocks-core
echo "== core's environment into a throwaway venv (fills the uv cache)"
uv sync --locked --extra http
echo "== v0.3.0 assets into assets-next (PSDI downloads; can take many minutes)"
uv run --locked --extra http goldilocks assets install workbench
echo "== throwaway core on :18000"
uv run --locked --extra http goldilocks serve http --host 127.0.0.1 --port 18000 &
pid=$!
ok=0
for i in $(seq 1 180); do
    if curl -sf -m 2 http://127.0.0.1:18000/health >/dev/null; then ok=1; break; fi
    sleep 1
done
[ "$ok" = 1 ] || { echo "core did not answer on :18000"; kill "$pid"; exit 1; }
python3 -I /smoke.py http://127.0.0.1:18000
kill "$pid"
echo "== PREWARM OK"
