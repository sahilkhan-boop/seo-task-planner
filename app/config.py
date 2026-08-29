"""Environment configuration. Loads a local .env file if present (never committed --
see .gitignore) so secrets don't need to be exported in every shell session.
"""
from __future__ import annotations

import os
import secrets

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

# Signs the login session cookie (see app/main.py's SessionMiddleware) -- set a real,
# persistent value in production (Render's dashboard) or every restart invalidates
# every logged-in session, forcing everyone to sign in again. Falls back to a
# freshly-generated one for local dev, where that's a harmless, expected annoyance
# rather than a real problem.
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32)

# Only a Google account with this exact email domain can sign in at all (see
# routers/auth.py) -- a plain env var, not hardcoded, so a non-Peppercontent
# deployment of this same codebase isn't stuck with someone else's domain.
ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "peppercontent.io")

# The login gate (app/main.py's require_login) makes sense for the shared Render
# deployment -- it's the whole point, restricting who can reach a URL anyone could
# otherwise guess -- but NOT for the standalone .exe (see run_desktop.py), which is
# a single person's own local copy with its own local, empty database: nothing
# shared to gatekeep, and no way to log in at all if they haven't set up Google
# OAuth credentials locally. run_desktop.py sets this to "false" before the app
# module loads; every other entry point (local dev, Render) defaults to requiring it.
LOGIN_REQUIRED = os.environ.get("LOGIN_REQUIRED", "true").strip().lower() not in ("false", "0", "no")


def google_oauth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def anthropic_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)
