> Written by Mat from the code. Sections "Overview" to "Hosting, build and release" describe version 1.6.2, which
> already exists and is released: nothing in them is to be built. Only the last two sections (sandbox, REQ-22;
> taskbar colours, REQ-28 / CR-1) are new work. Work stays paused until the owner says to continue.

# Architecture: AI Team Lite

## Overview (existing, 1.6.2)
A Windows desktop app. A local Python server (port 47824, token-protected) serves plain HTML/CSS/JavaScript screens
to a pywebview window (Edge WebView2). Live updates come from the server to the window as server-sent events. Fred,
Mat and the workers run through the Claude Agent SDK, which starts Claude's engine (claude.exe) as a child process.
The engine runs the team's file and shell commands itself. The app decides which of them need the user's approval.

## Parts (existing, 1.6.2)
| Part | Files | Job |
|---|---|---|
| Start | app/main.py, paths.py | Logging, hidden console, one instance per PC, opens the window (or browser with --browser); tray icon (class Tray, pystray: Open / Quit) when "keep running in the tray" is on. |
| Server | server.py, routes_setup.py, routes_projects.py, routes_work.py, server_common.py | HTTP routes for the buttons, SSE stream, voice endpoint. |
| Screens | app/ui/index.html, app.css, js/*.js | Chat, side panel (Team, Plan, Tests, Diagrams), settings, usage, checkpoints, scorecard. Load order: core, chat, side, composer, clipboard, spelling, checkpoints, scorecard, usage, screens, start. |
| Engine | engine.py + engine_watchdog, engine_limits, engine_pipeline, engine_text, engine_const | Owns all projects' sessions: watchdog, plan limits and sign-in expiry, master plan across projects. engine_pipeline.project_state gives each project's state: needs you, working, limit, waiting (for a slot), release, paused, idle. |
| Session | session.py + session_approvals, session_background, session_messages | One project's conversation with Claude: turns, approval cards, background workers, messages to events. |
| Team tools | tools.py + tools_setup, tools_planning, tools_building, tools_delivery, tools_common | The mcp__aiteam__* tools (plan approval, iterations, tests, findings, installer, GitHub...). |
| Guard | policy.py, shellcheck.py, risk.py | Decides what runs alone and what asks (L65); risk text on the cards. |
| Prompts | prompts.py, languages.py, skills.py, app/skills/* | Fred's, Mat's and the workers' instructions; bundled reviewed skills (do not edit). |
| Delivery | iterations.py, checkpoints.py, testrun.py, scorecard.py, tracking.py, freshtests.py, packaging.py, shipping.py, github.py | Iterations and Git commits, test runs on a hidden desktop, scorecard, installer, CI, releases. |
| Helpers | events.py, journal.py, ledger.py, usage.py, timeline.py, analytics.py, safefile.py, projectmap.py, upkeep.py, lessons.py | Event hub, recovery journal, token and time logs, safe JSON saves, project map, documents check, lessons. |
| Extras | voice.py, spelling.py, telegram.py, council.py, brains.py, diagrams.py, tooling.py, updates.py, features.py | Speech to text on the PC, spelling, phone approvals, council, Archify diagrams, Ollama/Archify install, updates, feedback. |
| Secrets & settings | secrets_store.py, settings.py, auth.py, reset.py | Keys in Windows Credential Manager (service "AI Team Lite"); settings JSON; Claude sign-in or API key; reset. |
| Windows helpers | winhelpers.py | Voice typing, screenshots, launching programs, hidden engine console, hidden test desktop. |

## Data and storage (existing, 1.6.2)
- App data: %APPDATA%\AI Team Lite (settings.json, logs, usage and time logs, lessons). No database: JSON and JSON-lines files.
- Per project: its own folder with the documents, code, tests, reports/ and a hidden .aiteam folder (project.json, events.jsonl = chat history, run journal). Shared JSON files are saved only through safefile.py (lock per file, retry on Windows' access denied).
- Secrets: Credential Manager only; never in files, prompts or chat.
- Backups: each project is a Git repository; checkpoints use Git (go back and undo). Optional push to GitHub with approval.

## Security (existing, 1.6.2)
- Approvals (policy.decide then shellcheck.judge): reading never asks; before plan approval every change asks; after it, work inside the project, Windows temp and the app's folder runs alone; installs, Windows changes, files elsewhere, programs from elsewhere, private files and GitHub ask. Every team tool must be in a policy list (a test checks it).
- The weak point: commands run with the user's full rights. Text patterns are the only guard, and a tricked agent or a bad package can get round a pattern.
- Network: osv.dev (package names and versions only), Anthropic, GitHub, optionally Gemini and Telegram.

## Hosting, build and release (existing, 1.6.2)
- Runs only on the user's PC. Bundled Python; NSIS installer (installer/ai-team.nsi, build.sh), unsigned.
- GitHub Actions: tests.yml on every push; release.yml builds and publishes a version on every push to main (so: never push without the owner's approval; bump app/__init__.py, VERSION and CHANGELOG.md together).

## New work: sandbox for the team's commands (REQ-22)
Owner's PC: Windows 11 Home, so Windows Sandbox is ruled out; WSL is ruled out because projects are Windows apps.

Chosen direction to investigate: **B. AppContainer.**
- Where it fits: the engine is started in one place, winhelpers.hide_engine_console (it wraps anyio.open_process). The sandbox would start claude.exe inside an AppContainer from there, so every command the engine runs inherits it. testrun.run (tests on the hidden desktop) and "Launch the project" are started by the app and get the same container.
- Access granted: the project folder (read and write), the bundled Python and Git (read and run), Windows temp for the project, the app's data folder only where the engine needs it. Everything else in the user profile is denied.
- Protects first: 1. writing outside the project; 2. reading private files (keys, browser data, documents). Network stays open (pip, GitHub, Anthropic need it); installs and pushes keep asking on the cards.
- Behind a setting, on by default once proven; when a command runs outside the protection, the approval card says so in one clear line. policy.py and shellcheck.py stay as the second guard.
- Step 1 is an investigation with a prototype: reports/L47-sandbox-options.md, proving that Python, Git, pip and the tests work inside the container on this PC. Option A (restricted token + job object) is the fallback if B breaks them. Step 2 is built only after the owner says "go".
- Open points to prove on the real PC: claude.exe sign-in and its own config folder from inside the container, Git credential access for pushes, pip's cache, WebView2/pywebview apps launched by tests.

## New work: taskbar colours across all projects (REQ-28, CR-1)
- **One combined state**, computed from engine.project_state for every project (no new bookkeeping):
  - orange "Waiting for you": any project in "needs you" (question, approval card, sign-in card) or "release" (ready for your review);
  - green "Working": none orange, and at least one project "working", "waiting" (queued for a slot) or "limit" (will resume by itself);
  - grey "Nothing running": everything else (paused, idle, no projects).
  Orange wins over green. Tooltip in words, e.g. "AI Team Lite: 1 project waiting for you".
- **Where it is computed**: a small new module app/statusicon.py (pure function: list of project states -> colour and tooltip text), easy to unit-test. A light loop in main.py checks it every 2 seconds and acts only when the result changes.
- **Taskbar**: a coloured dot as a Windows overlay badge on the window's taskbar button (ITaskbarList3.SetOverlayIcon via ctypes/comtypes on the pywebview window handle). The dot images are drawn once with Pillow (already a dependency).
- **Flashing**: when the state turns orange, the taskbar button flashes (FlashWindowEx) until the card is answered; it stops while the AI Team Lite window is in front and starts again if the window goes to the back and something still waits; it stops when the state leaves orange. New setting "Flash the taskbar until I answer" (settings.py, on by default; Settings > General). The orange dot stays whether or not flashing is on.
- **Tray**: when "keep running in the tray" is on, the tray icon (Tray class) shows the same dot and tooltip.
- **Fallback**: if the badge, flashing or tray cannot be set (non-Windows, no handle yet, COM error), it is logged once and the normal icon stays; the rest of the app is unaffected.
- **Tests**: unit tests for the colour rules and the flashing decisions (statusicon.py), a Windows screen check that the badge changes (screenshot of the taskbar button).

## After approval
Work stays paused until the owner asks to continue. Then: sandbox investigation, sandbox build (after "go"), taskbar colours.

## Decisions
| Topic | Decision | Why |
|---|---|---|
| Existing version | 1.6.2 is released and is described as it is; nothing in it is rebuilt | Owner's correction |
| Sandbox approach | Investigate AppContainer (B) first, restricted token (A) as fallback | Owner's choice; works on Windows 11 Home with no admin |
| Sandbox protection order | 1. writing outside the project, 2. reading private files; network open | Owner agreed |
| Taskbar colour display | Coloured dot as a Windows overlay badge on the app icon | Owner chose A: app stays recognisable, standard Windows mechanism |
| Taskbar flashing | Flash until the card is answered, paused while the window is in front, with a setting on by default | Owner wants continuous flashing; the setting and the pause cover meetings, screen sharing and comfort |
| Source of the combined state | engine.project_state for all projects; orange = needs you or ready for review, green = working, queued or limit, grey = the rest | Already tracked for the Team panel and Pipeline |
| Storage | One new setting in settings.json; no other data | Nothing else needs saving |
