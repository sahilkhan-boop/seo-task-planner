# Building the standalone .exe

For handing the tool to a teammate who doesn't have Python installed -- a
portable copy with its own local database, separate from whatever's running on
this machine. Not how you run this app day to day; for that, see the README's
`uvicorn app.main:app` instructions.

## Build

```
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --onefile --name SEOTaskPlanner ^
  --add-data "app/templates;app/templates" ^
  --add-data "app/static;app/static" ^
  --collect-all reportlab ^
  --collect-all anthropic ^
  run_desktop.py
```

Output: `dist\SEOTaskPlanner.exe` (~40MB, single file). `build\` and
`SEOTaskPlanner.spec` are build artifacts -- safe to delete, both gitignored.

## What NOT to bundle

`.env` (real Google/Anthropic credentials) and `seo_analyzer.db` (real client
data) must never be added to `--add-data` or copied into `dist\`. Each
teammate's copy is meant to start with its own empty database and their own
credentials -- see app/paths.py's module docstring for why the DB/`.env`
resolve next to the running .exe (APP_DIR) while templates/static resolve from
the bundled, read-only temp extraction dir (RESOURCE_DIR / `sys._MEIPASS`).

## What to hand a teammate

Zip together:
- `dist\SEOTaskPlanner.exe`
- `.env.example` (renamed/copied so they have something to fill in)

Their setup:
1. Unzip anywhere.
2. Copy `.env.example` to `.env` in the same folder as the .exe, fill in their
   own `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `ANTHROPIC_API_KEY` (see the
   main README's "GSC connection setup" section for how to create these) --
   optional, the app runs fine without it, just without GSC/GA4 sync or the chat
   feature until it's filled in.
3. Double-click `SEOTaskPlanner.exe`. A console window opens (leave it running --
   closing it stops the server) and the app opens in their default browser at
   `http://127.0.0.1:8123`.
4. `seo_analyzer.db` and an `imports\` folder are created next to the .exe on
   first run -- their own local data, not shared with anyone else's copy.

## Verified

Built and smoke-tested 2026-08-26: ran from a clean folder outside the repo,
confirmed a fresh empty DB + `imports/` folder get created next to the .exe (not
inside it), and that Sites/Tasks/Benchmarks pages render with static CSS
correctly from the bundled templates/static.
