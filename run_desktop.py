"""Entry point for the packaged .exe (see build_exe.py / the PyInstaller spec).

Not used when running from source -- that's still `uvicorn app.main:app`
directly, per the README. This script exists only because a frozen exe has no
shell to run that command in: it starts the same FastAPI app in-process and
opens the default browser to it, so a teammate can just double-click the .exe.
"""
from __future__ import annotations

import os

# Must be set before `app.config` first loads (below, via app.main) -- a single
# person's own local copy has nothing shared to gatekeep, and no way to complete a
# Google login at all without their own OAuth credentials configured. See
# app/config.py's own comment on LOGIN_REQUIRED for the full reasoning.
os.environ.setdefault("LOGIN_REQUIRED", "false")

import threading
import time
import webbrowser

import uvicorn

from app.main import app
from app.paths import APP_DIR, IMPORTS_DIR

HOST = "127.0.0.1"
PORT = 8123


def _open_browser_once_ready() -> None:
    # The server needs a moment to bind the port before a browser tab pointed at
    # it would succeed -- a short fixed delay is simpler and plenty reliable here
    # rather than polling the socket, since this is a one-person local tool, not
    # something needing to start instantly.
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}/sites")


if __name__ == "__main__":
    # Running from source, this folder ships in the repo (imports/.gitkeep) -- a
    # fresh frozen .exe on a teammate's machine has no repo, so create it here
    # instead of making them find and make it themselves before their first import.
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"SEO Task Planner starting -- data directory: {APP_DIR}")
    print(f"Opening http://{HOST}:{PORT} in your browser. Close this window to stop the server.")
    threading.Thread(target=_open_browser_once_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
