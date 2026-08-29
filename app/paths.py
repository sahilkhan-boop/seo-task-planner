"""Filesystem paths that need to work identically whether the app is run from
source (`python -m uvicorn app.main:app`) or as a PyInstaller-frozen .exe handed
to a teammate -- the two have very different notions of "where am I".

Two different directories matter, and conflating them is the classic PyInstaller
onefile mistake:

- APP_DIR: where the running .exe itself lives (frozen) or the project root
  (source). Writable, and persists across runs/reboots -- this is where
  per-teammate USER DATA belongs: the SQLite DB, a dropped-in .env with their own
  Google/Anthropic credentials, uploaded crawl-import folders. Never bundle real
  data here into the build itself; each teammate's copy starts empty.
- RESOURCE_DIR: where bundled READ-ONLY assets (Jinja templates, static CSS) can
  be found. Running from source this is the same as APP_DIR. Frozen via
  `--onefile`, PyInstaller extracts bundled data into a temp directory
  (`sys._MEIPASS`) fresh on every launch -- using that for the DB/`.env` would
  silently lose data (or real secrets, if it were ever bundled) the moment the
  process exits and the temp dir gets cleaned up.
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = APP_DIR

TEMPLATES_DIR = RESOURCE_DIR / "app" / "templates"
STATIC_DIR = RESOURCE_DIR / "app" / "static"

DB_PATH = APP_DIR / "seo_analyzer.db"
ENV_PATH = APP_DIR / ".env"
IMPORTS_DIR = APP_DIR / "imports"
