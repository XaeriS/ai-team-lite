> Written by Mat from the code of version 1.6.2 (described as it is), plus the planned sandbox (REQ-22).

# Architecture: AI Team Lite

## Overview
A Windows desktop app. A local Python server (port 47824, token-protected) serves plain HTML/CSS/JavaScript screens
to a pywebview window (Edge WebView2). Live updates come from the server to the window as server-sent events. Fred,
Mat and the workers run through the Claude Agent SDK, which starts Claude's engine (claude.exe) as a child process.
The engine runs the team's file and shell commands itself. The app decides which of them need the user's approval.

## Parts
| Part | Files | Job |
|---|---|---|
| Start | app/main.py, paths.py | Logging, hidden console, one instance per PC, opens the window (or browser with --browser). |
| Server | server.py, routes_setup.py, routes_projects.py, routes_work.py, server_common.py | HTTP routes for the buttons, SSE stream, voice endpoint. |
| Screens | app/ui/index.html, app.css, js/*.js | Chat, side panel (Team, Plan, Tests, Diagrams), settings, usage, checkpoints, scorecard. Load order: core, chat, side, composer, clipboard, spelling, checkpoints, scorecard, usage, screens, start. |
| Engine | engine.py + engine_watchdog, engine_limits, engine_pipeline, engine_text, engine_const | Owns all projects' sessions: watchdog, plan limits and sign-in expiry, master plan across projects. |
| Session | session.py + session_approvals, session_background, session_messages | One project's conversation with Claude: turns, approval cards, background workers, messages to events. |
| Team tools | tools.py + tools_setup, tools_planning, tools_building, tools_delivery, tools_common | The mcp__aiteam__* tools (plan approval, iterations, tests, findings, installer, GitHub...). |
| Guard | policy.py, shellcheck.py, risk.py | Decides what runs alone and what asks (L65); risk text on the cards. |
| Prompts | prompts.py, languages.py, skills.py, app/skills/* | Fred's, Mat's and the workers' instructions; bundled reviewed skills (do not edit). |
| Delivery | iterations.py, checkpoints.py, testrun.py, scorecard.py, tracking.py, freshtests.py, packaging.py, shipping.py, github.py | Iterations and Git commits, test runs on a hidden desktop, scorecard, installer, CI, releases. |
| Helpers | events.py, journal.py, ledger.py, usage.py, timeline.py, analytics.py, safefile.py, projectmap.py, upkeep.py, lessons.py | Event hub, recovery journal, token and time logs, safe JSON saves, project map, documents check, lessons. |
| Extras | voice.py, spelling.py, telegram.py, council.py, brains.py, diagrams.py, tooling.py, updates.py, features.py | Speech to text on the PC, spelling, phone approvals, council, Archify diagrams, Ollama/Archify install, updates, feedback. |
| Secrets & settings | secrets_store.py, settings.py, auth.py, reset.py | Keys in Windows Credential Manager (service "AI Team Lite"); settings JSON; Claude sign-in or API key; reset. |

## Data and storage
- App data: %APPDATA%\AI Team Lite (settings.json, logs, usage and time logs, lessons). No database: JSON and JSON-lines files.
- Per project: its own folder with the documents, code, tests, reports/ and a hidden .aiteam folder (project.json, events.jsonl = chat history, run journal). Shared JSON files are saved only through safefile.py (lock per file, retry on Windows' access denied).
- Secrets: Credential Manager only; never in files, prompts or chat.
- Backups: each project is a Git repository; checkpoints use Git (go back and undo). Optional push to GitHub with approval.

## Security today
- Approvals (policy.decide then shellcheck.judge): reading never asks; before plan approval every change asks; after it, work inside the project, Windows temp and the app's folder runs alone; installs, Windows changes, files elsewhere, programs from elsewhere, private files and GitHub ask. Every team tool must be in a policy list (a test checks it).
- The weak point: commands run with the user's full rights. Text patterns are the only guard, and a tricked agent or a bad package can get round a pattern.
- Network: osv.dev (package names and versions only), Anthropic, GitHub, optionally Gemini and Telegram.

## Hosting, build and release
- Runs only on the user's PC. Bundled Python; NSIS installer (installer/ai-team.nsi, build.sh), unsigned.
- GitHub Actions: tests.yml on every push; release.yml builds and publishes a version on every push to main (so: never push without the owner's approval; bump app/__init__.py, VERSION and CHANGELOG.md together).

## Planned: sandbox for the team's commands (REQ-22)
Owner's PC: Windows 11 Home, so Windows Sandbox is ruled out; WSL is ruled out because projects are Windows apps.

Chosen direction to investigate: **B. AppContainer.**
- Where it fits: the engine is started in one place, winhelpers.hide_engine_console (it wraps anyio.open_process). The sandbox would start claude.exe inside an AppContainer from there, so every command the engine runs inherits it. testrun.run (tests on the hidden desktop) and "Launch the project" are started by the app and get the same container.
- Access granted: the project folder (read and write), the bundled Python and Git (read and run), Windows temp for the project, the app's data folder only where the engine needs it. Everything else in the user profile is denied.
- Protects first: 1. writing outside the project; 2. reading private files (keys, browser data, documents). Network stays open (pip, GitHub, Anthropic need it); installs and pushes keep asking on the cards.
- Behind a setting, on by default once proven; when a command runs outside the protection, the approval card says so in one clear line. policy.py and shellcheck.py stay as the second guard.
- Step 1 is an investigation with a prototype: reports/L47-sandbox-options.md, proving that Python, Git, pip and the tests work inside the container on this PC. Option A (restricted token + job object) is the fallback if B breaks them. Step 2 is built only after the owner says "go".
- Open points to prove on the real PC: claude.exe sign-in and its own config folder from inside the container, Git credential access for pushes, pip's cache, WebView2/pywebview apps launched by tests.

## Decisions
| Topic | Decision | Why |
|---|---|---|
| Structure | Keep the current split (engine/session/server/tools mixins and route files, files under about 500 lines) | Done in 1.5.1 and tested; no reason to change it |
| Storage | JSON and JSON-lines files, saved through safefile.py; no database | Single user, small data, already working |
| Secrets | Windows Credential Manager only | Never in files, prompts or chat |
| Backups | Git per project, checkpoints, optional GitHub push with approval | Already built (REQ-15, REQ-19) |
| Hosting | Local only, on the user's Windows PC | Desktop app by design |
| Sandbox approach | Investigate AppContainer (B) first, restricted token (A) as fallback | Owner's choice; works on Windows 11 Home with no admin setup |
| Sandbox protection order | 1. writing outside the project, 2. reading private files; network stays open | Owner agreed; pip and GitHub need the network, approval cards still guard installs and pushes |
