"""A chat's generated input bundles, kept on the agent server (#142).

`dft_download_bundle` writes each bundle core generates under the chat's own
directory, and the chat's Download card links to it, so the file is still
there after a reload. A thread's bundles go with the thread: deleted by
chat retention on the website and by deleting a conversation locally.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from goldilocks_agent import store

# A thread id is a uuid, or the frontend's own fallback id: never a path.
_THREAD_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Bundles kept per chat; the oldest goes first.
KEEP_PER_THREAD = 5


def _bundles_root() -> Path:
    # Next to the database (design doc 16.1: tables keep paths, files stay
    # on disk), so wherever the database lives the bundles follow.
    return store.DB_PATH.parent / "bundles"


def thread_dir(thread_id: str) -> Path:
    """The directory `thread_id`'s bundles live in. Refuses an id that
    isn't one, so no request can reach outside it."""
    if not _THREAD_ID.match(thread_id):
        raise ValueError(f"Not a chat id: {thread_id!r}")
    return _bundles_root() / thread_id


def save_bundle(thread_id: str, filename: str, content: bytes) -> Path:
    """Writes a bundle for `thread_id` (replacing one of the same name) and
    drops the oldest past `KEEP_PER_THREAD`. Returns its path."""
    directory = thread_dir(thread_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(filename).name
    path.write_bytes(content)
    bundles = sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    for old in bundles[:-KEEP_PER_THREAD]:
        old.unlink()
    return path


def bundle_path(thread_id: str, filename: str) -> Path | None:
    """The kept bundle `filename` of `thread_id`, or None. A filename that
    isn't a plain name, or a bundle that isn't there, is None."""
    if Path(filename).name != filename or not filename.endswith(".zip"):
        return None
    path = thread_dir(thread_id) / filename
    return path if path.is_file() else None


def download_url(thread_id: str, filename: str) -> str:
    """Where the browser downloads a kept bundle."""
    return f"/api/chat/{thread_id}/dft/bundles/{filename}"


def delete_bundles(thread_id: str) -> None:
    """Deletes every bundle kept for `thread_id`."""
    if _THREAD_ID.match(thread_id):
        shutil.rmtree(_bundles_root() / thread_id, ignore_errors=True)
