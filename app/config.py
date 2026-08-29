"""Environment configuration. Loads a local .env file if present (never committed --
see .gitignore) so secrets don't need to be exported in every shell session.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from app.paths import ENV_PATH

# Explicit path, not the default load_dotenv() CWD-search -- a frozen .exe can be
# launched with any working directory (double-click, shortcut, a "Start in" set to
# something else), but the teammate's own .env always lives next to the .exe
# itself (see app/paths.py). override=False (the default) means a real
# already-exported environment variable still wins over the file, same as before.
load_dotenv(ENV_PATH)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


def google_oauth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def anthropic_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)
