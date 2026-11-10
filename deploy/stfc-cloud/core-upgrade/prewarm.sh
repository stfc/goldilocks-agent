# Runs inside the NEW agent image (README.md "Upgrading goldilocks-core"):
# prepares the pinned goldilocks-core's environment and assets without
# touching what the live container uses, then starts a throwaway core on
# :18000 and smoke-tests it.
set -eu
A="$HOME/.local/share/goldilocks"
cd /opt/goldilocks-core
VERSION=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
echo "== goldilocks-core $VERSION"
echo "== copy the live assets to assets-next (the live directory is only read)"
rm -rf "$A/assets-next"
cp -a "$A/assets" "$A/assets-next"
echo "== core's environment into a throwaway venv (fills the uv cache)"
uv sync --locked --extra http
echo "== assets into assets-next (PSDI downloads; can take many minutes)"
uv run --locked --extra http goldilocks assets install all
echo "== throwaway core on :18000"
uv run --locked --extra http goldilocks serve http --host 127.0.0.1 --port 18000 &
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
