# Deploying to a Windows work laptop

This app was built on a Mac (Python 3.14, zsh). No git repo exists for this
project yet, so getting it onto a second machine is a manual copy, not a
`git clone`. This doc is the checklist for that move.

## Copy these folders/files

- `app/` — all application code, templates, static assets
- `tests/`
- `scripts/` — includes `generate_workflow_pdf.py`
- `imports/.gitkeep` (just the folder structure — real crawl exports are
  machine-local and regenerated per Screaming Frog run, not copied)
- `requirements.txt`
- `README.md`
- `.env.example`
- `.gitignore`
- `docs/` (this file)

## Do NOT copy as-is

- **`.venv/`** — Mac-built virtualenv won't run on Windows; recreate fresh there.
- **`__pycache__/`, `.pytest_cache/`** — regenerate on first run/test.
- **`*.db`** (the SQLite file) — decide first: fresh DB on Windows (most
  likely right, since Screaming Frog/GSC data will be re-synced there
  anyway), or deliberately carry the Mac's DB over if Windows should pick up
  mid-campaign. Don't copy it by default without deciding this.
- **`.env`** — contains the real Anthropic API key + (eventually) Google
  OAuth secret. Don't transfer over email/Slack/cloud drive in plain text;
  retype the values into a fresh `.env` on Windows (copied from
  `.env.example`), or use a password manager/secrets tool to move them.

## Windows setup (Command Prompt)

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
copy .env.example .env
REM then edit .env in a text editor and fill in the real values
uvicorn app.main:app --reload
```

Needs Python 3.11+ installed on Windows first (this project was built on
3.14; nothing in the code needs anything version-specific beyond ~3.11, so a
slightly older 3.x on the work laptop is fine).

## Every Mac → Windows command substitution

| Step | macOS/zsh (how this was built) | Windows |
|---|---|---|
| Create venv | `python3 -m venv .venv` | `python -m venv .venv` (Windows' launcher is just `python`, not `python3`) |
| Activate venv | `source .venv/bin/activate` | Command Prompt: `.venv\Scripts\activate.bat` — PowerShell: `.venv\Scripts\Activate.ps1` |
| Run the app | `uvicorn app.main:app --reload` | same, once the venv is activated — no change |
| Run tests | `pytest -q` / `.venv/bin/python -m pytest -q` | `pytest -q` / `.venv\Scripts\python -m pytest -q` |
| Regenerate workflow PDF | `python scripts/generate_workflow_pdf.py` | same, no change |

### PowerShell script-execution gotcha

PowerShell blocks running `Activate.ps1` by default ("cannot be loaded
because running scripts is disabled on this system"). Fix for the current
session only (doesn't need admin):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then `.venv\Scripts\Activate.ps1` works. Using Command Prompt's
`activate.bat` instead sidesteps this entirely, which is why the setup steps
above default to Command Prompt.

### Screaming Frog CLI path/binary differs on Windows

It's a different executable name, not the macOS `.app` bundle path
documented in `app/ingestion/screaming_frog.py`'s docstring and
`app/templates/imports.html`. Windows default install location is typically:

```bat
"C:\Program Files (x86)\Screaming Frog SEO Spider\ScreamingFrogSEOSpiderCli.exe" ^
  --crawl https://yoursite.com --headless ^
  --output-folder imports\yoursite.com\2026-08-15 ^
  --export-tabs "Response Codes:All" ^
  --bulk-export "Links:All Inlinks" ^
  --save-report "Redirects:Redirect Chains"
```

(Windows batch line-continuation is `^`, not `\`.) Only matters if crawls
will actually be run from the Windows machine. Per the same pattern used
when this was first verified on Mac, confirm the exact flags against
Windows' own `--help` / `--help export-tabs` / `--help bulk-export` output
rather than assuming they're identical — don't take this table on faith.

### One thing to verify on first Windows run (not a known bug)

`app/db.py` builds the SQLite URL via `"sqlite:///" + os.path.join(...)`,
and `os.path.join` produces backslash-separated paths on Windows (e.g.
`sqlite:///C:\Users\...\seo_analyzer.db`). SQLAlchemy/`sqlite3` generally
handle this fine, but it's untested on Windows — if `init_db()` fails on
first run with a path/URL error, the fix is rebuilding that line with
`pathlib.Path(...).as_posix()` instead of `os.path.join` + string
concatenation.
