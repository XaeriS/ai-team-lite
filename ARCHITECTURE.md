> Written by Mat from the code. Sections "Overview" to "Hosting, build and release" describe version 1.6.2, which
> already exists and is released: nothing in them is to be built. The sections marked "New work" are new:
> REQ-22 (sandbox), REQ-28 (taskbar colours, CR-1), REQ-29 (drafts per project, CR-2), REQ-30 (improvement ideas, CR-3).

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
| Screens | app/ui/index.html, app.css, js/*.js | Chat, side panel (Team, Plan, Tests, Diagrams), settings, usage, checkpoints, scorecard. One shared message box (composer.js). Load order: core, chat, side, composer, clipboard, spelling, checkpoints, scorecard, usage, screens, start. |
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
Investigation done (iteration 11, reports/L47-sandbox-options.md, tools/sandbox_probe.py): AppContainer ran Python, Git, pip and claude.exe and refused writing outside and reading a canary file; pytest needs "read attributes" on the parent folder of the project; a restricted token refused neither.

Chosen direction: **B. AppContainer** (built only after the owner's yes).
- Where it fits: the engine is started in one place, winhelpers.hide_engine_console (it wraps anyio.open_process). The sandbox would start claude.exe inside an AppContainer from there, so every command the engine runs inherits it. testrun.run (tests on the hidden desktop) and "Launch the project" are started by the app and get the same container.
- Access granted: the project folder (read and write), read attributes on its parent folder, the bundled Python and Git (read and run), Windows temp for the project, the app's data folder only where the engine needs it. Everything else in the user profile is denied.
- Protects first: 1. writing outside the project; 2. reading private files (keys, browser data, documents). Network stays open (pip, GitHub, Anthropic need it); installs and pushes keep asking on the cards.
- Behind a setting, on by default once proven; when a command runs outside the protection, the approval card says so in one clear line. policy.py and shellcheck.py stay as the second guard.
- Still to prove on the real PC before switching it on by default: claude.exe sign-in and its own config folder, Git credentials for pushes, pip install online, apps with a window launched by tests.

## New work: taskbar colours across all projects (REQ-28, CR-1)
- **One combined state**, computed from engine.project_state for every project (no new bookkeeping):
  - orange "Waiting for you": any project in "needs you" (question, approval card, sign-in card, ideas card) or "release" (ready for your review);
  - green "Working": none orange, and at least one project "working", "waiting" (queued for a slot) or "limit" (will resume by itself);
  - grey "Nothing running": everything else (paused, idle, no projects).
  Orange wins over green. Tooltip in words, e.g. "AI Team Lite: 1 project waiting for you".
- **Where it is computed**: a small new module app/statusicon.py (pure function: list of project states -> colour and tooltip text). A light loop in main.py checks it every 2 seconds and acts only when the result changes.
- **Taskbar**: a coloured dot as a Windows overlay badge on the window's taskbar button (ITaskbarList3.SetOverlayIcon via ctypes on the pywebview window handle). Dot images drawn once with Pillow.
- **Flashing**: when the state turns orange, the taskbar button flashes (FlashWindowEx) until the card is answered; it pauses while the AI Team Lite window is in front and starts again if the window goes to the back while something still waits; it stops when the state leaves orange. Setting "Flash the taskbar until I answer" (settings.py, on by default; Settings > General). The orange dot stays either way.
- **Tray**: when "keep running in the tray" is on, the tray icon shows the same dot and tooltip.
- **Fallback**: if the badge, flashing or tray cannot be set, it is logged once and the normal icon stays.
- **Tests**: unit tests for the colour rules and flashing decisions; a Windows screen check of the taskbar button.

## New work: one message draft per project (REQ-29, CR-2)
- **In the window**: composer.js keeps a draft per project (text plus the list of attachments) in memory. On switching projects, the current box is saved to the old project's draft and the new project's draft is put in the box (empty if none). Sending clears only that project's draft. Spelling marks are redrawn for the restored text.
- **Saved with the project**: the draft is also saved to the server about 1.5 seconds after typing stops, and when switching projects or closing the window, through a new route (routes_work.py: GET/PUT /api/projects/{slug}/draft). The server stores it in the project's .aiteam/draft.json through safefile.py. Attachments are saved as references to the files already uploaded for that project (no second copy); an attachment whose file is gone is dropped with a short note under the box.
- **Opening a project** loads its draft from the server. A draft is never sent to Claude until the user presses Send.
- **Privacy**: the draft stays in the project folder on this PC (like the chat history, events.jsonl). .aiteam/ is already kept out of Git.
- **Tests**: unit test of the route and the file (save, load, clear on send, missing attachment); a browser test (type in project A, switch to B: box empty; back to A: text and attachments back; restart the server: still there).

## New work: improvement ideas after each iteration (REQ-30, CR-3)
- **Trigger**: when commit_iteration succeeds (not for already-done or parked iterations), its result tells Fred to prepare ideas, so Fred does it as his next step, before moving on. It runs after every iteration, including the clean-up.
- **Where ideas come from**: the Scout (a read-only, web-searching worker, already allowed before and after the plan) searches briefly for what similar tools offer, with a cap of a few searches; Fred adds what the project's documents and code suggest. Fred filters out anything already in REQUIREMENTS.md, the plan, the parked list, or the decision log, keeps at most 3, and sends nothing if none is worth it.
- **New team tool** propose_ideas(ideas=[{title, why, effort, source_link?}]) in tools_planning.py (policy list ALWAYS_ALLOW, like record_challenge_review). It refuses an idea whose title matches a decided one (simple text comparison of normalised titles), so an idea is never shown twice even if Fred forgets.
- **Card**: a new "ideas" card in the chat (session_approvals, like the devil's advocate cards): a table with idea, why it helps, effort and a link when it came from the web, and Yes / No buttons per row, plus an optional reason box. The project's state is "needs you" while the card is open, so the taskbar turns orange and the phone gets it if Telegram is on.
- **Answers**: Yes -> Fred records a change request for it (the normal CR card with impact, then the plan). No -> never proposed again. The work continues with the next iteration once every row is answered.
- **Decision log**: .aiteam/ideas.jsonl (one line per decision: id, title, why, effort, source, decision yes/no, date, reason) through ledger.py, plus reports/IDEAS.md rebuilt from it after each decision (a readable table, kept in Git like the other reports).
- **Cost**: one short Scout run and one short Fred step per iteration; skipped when the plan limit is near (Fred says so in one line and proposes at the next iteration).
- **Tests**: unit tests of the tool (cap of 3, no repeat of decided ideas, log and IDEAS.md written), a fake-Claude flow test (iteration committed -> ideas card -> Yes creates a CR, No is logged), a browser test of the card.

## After approval
Work order: traceability of existing tests (recording the proof for REQ-1 to REQ-22), then the sandbox build if the owner says yes, then the taskbar colours, the drafts and the ideas, each its own iteration. Plan usage is high: big steps wait for the owner's go.

## Decisions
| Topic | Decision | Why |
|---|---|---|
| Drafts storage | Per project in .aiteam/draft.json through safefile.py, saved 1.5 s after typing stops and on switch/close | Owner wants drafts to survive closing the app |
| Draft attachments | Saved as references to the project's uploaded files, no second copy | Keeps disk use small; a missing file is dropped with a note |
| Source of ideas | Scout web search (capped) plus Fred's reading of the project | Owner chose richer ideas over the lowest cost |
| Ideas card and tool | New propose_ideas tool and an ideas card with Yes/No per row, like the devil's advocate cards | Same pattern as existing cards, works on the phone too |
| Decision log | .aiteam/ideas.jsonl plus reports/IDEAS.md | Machine log prevents repeats; readable report for the owner and IT |
| Trigger | After each successful commit_iteration, before the next iteration; skipped near the plan limit | Owner asked for it after every iteration; protects the plan usage |
| Earlier decisions kept | 1.6.2 described as is; sandbox AppContainer pending the owner's yes; taskbar dot and flashing as approved | Already approved |
