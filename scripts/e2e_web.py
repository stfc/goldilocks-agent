"""Run the web build's end-to-end tests (app/e2e) against a local staging agent.

The staging agent is set up like the STFC VM's:
- the shared-deployment frontend (`npm run build:stfc-cloud`);
- GOLDILOCKS_AGENT_SHARED_DEPLOYMENT=1;
- the chat model pinned to local Ollama's qwen3.8;
- goldilocks-core auto-started from PyPI at core_pin.json's version (the VM
  runs the same release from a checkout).

HOME is a fresh temporary directory, so no API keys or saved preferences
leak in, and test chats don't land in your own ~/.goldilocks. uv's cache and
Python installs and core's assets stay where they are.

Usage: `uv run poe e2e-web [playwright args]`, e.g. `-- -g ranking`.
`uv run poe e2e-web --serve` starts the staging agent and leaves it running
(Ctrl-C stops it), to try the web build by hand.
Needs Ollama running with the model, `npm install` in app/,
`npx playwright install chromium` once, and ports 8096 and 8000 free.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
PORT = int(os.environ.get("E2E_PORT", "8096"))
CORE_PORT = 8000  # core_server.CORE_SERVER_BASE_URL
MODEL = os.environ.get("E2E_MODEL", "ollama_chat/qwen3.8:latest")
BASE_URL = f"http://127.0.0.1:{PORT}"
CORE_READY_TIMEOUT = 20 * 60  # a first start installs core's environment


def _port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _post_json(url: str) -> dict:
    request = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _wait_for_agent(server: subprocess.Popen) -> None:
    for _ in range(120):
        if server.poll() is not None:
            sys.exit(f"the staging agent exited with {server.returncode}")
        if _port_in_use(PORT):
            return
        time.sleep(0.5)
    sys.exit("the staging agent did not start listening")


def _wait_for_core() -> None:
    deadline = time.monotonic() + CORE_READY_TIMEOUT
    last = None
    while time.monotonic() < deadline:
        status = _post_json(f"{BASE_URL}/api/core-server/ensure")
        line = f"core: {status['status']} {status.get('detail') or ''}".strip()
        if line != last:
            print(line, flush=True)
            last = line
        if status["status"] == "ready":
            return
        if status["status"] in ("error", "not_configured"):
            sys.exit(line)
        time.sleep(5)
    sys.exit("goldilocks-core did not become ready")


def main() -> int:
    for port, what in ((PORT, "the staging agent"), (CORE_PORT, "goldilocks-core")):
        if _port_in_use(port):
            sys.exit(f"port {port} ({what}) is in use; stop whatever holds it first")

    real_home = Path.home()
    uv_cache = os.environ.get("UV_CACHE_DIR", str(real_home / ".cache" / "uv"))
    uv_pythons = str(real_home / ".local" / "share" / "uv" / "python")
    assets = os.environ.get(
        "GOLDILOCKS_ASSET_ROOT",
        str(real_home / ".local" / "share" / "goldilocks" / "assets"),
    )
    with tempfile.TemporaryDirectory(prefix="goldilocks-e2e-") as tmp:
        dist = Path(tmp) / "dist"
        subprocess.run(
            ["npm", "run", "build:stfc-cloud", "--", "--outDir", str(dist)],
            cwd=APP_DIR,
            check=True,
        )
        home = Path(tmp) / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "UV_CACHE_DIR": uv_cache,
            "UV_PYTHON_INSTALL_DIR": uv_pythons,
            "GOLDILOCKS_ASSET_ROOT": assets,
            "GOLDILOCKS_AGENT_SHARED_DEPLOYMENT": "1",
            "GOLDILOCKS_AGENT_MODEL": MODEL,
            "GOLDILOCKS_AGENT_CORE_AUTOSTART": "1",
            "GOLDILOCKS_AGENT_STATIC_DIR": str(dist),
        }
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "goldilocks_agent.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=REPO_ROOT,
            env=env,
        )
        try:
            _wait_for_agent(server)
            _wait_for_core()
            print(f"staging agent ready at {BASE_URL} (model {MODEL})", flush=True)
            if "--serve" in sys.argv[1:]:
                print("serving until Ctrl-C", flush=True)
                with contextlib.suppress(KeyboardInterrupt):
                    server.wait()
                return 0
            # `poe e2e-web -- -g x` passes the `--` on; Playwright would take
            # what follows it as file filters.
            extra = sys.argv[1:]
            if extra[:1] == ["--"]:
                extra = extra[1:]
            return subprocess.run(
                ["npx", "playwright", "test", "-c", "e2e/playwright.config.ts", *extra],
                cwd=APP_DIR,
                env={**os.environ, "E2E_BASE_URL": BASE_URL},
            ).returncode
        finally:
            # The agent's shutdown stops the core it started (core_server's
            # own process group).
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
