# Plan

## Goal
Carry on from AI Team Lite 1.6.2 (released, nothing rebuilt). Two pieces of new work, in this order:
1. A sandbox so a bad package or a tricked agent cannot write outside the project or read your private files (REQ-22).
2. The taskbar icon changes with the state of all projects (REQ-28, CR-1), with the choices you made: an orange, green or grey dot on the icon, and flashing until you answer.

Work stays paused until you ask me to continue. Overview diagram: `.archify/architecture-ai-team-lite-20261121-085038/architecture-ai-team-lite.html`.

## Already done (shown as done, not rebuilt)
REQ-1 to REQ-21, released up to 1.6.2, grouped into five finished iterations below.

## Still to do
1. **Sandbox investigation (no release, no change to the app).** Report `reports/L47-sandbox-options.md` plus a prototype on this Windows 11 Home PC showing whether AppContainer works for Python, Git, pip, the tests and claude.exe (restricted token as fallback). Ends with one recommendation and a question to you. Tested by a script that records pass or fail per tool.
2. **Build the sandbox (only after you say "go" on the report).** Behind a setting; engine and test runs start inside it; the approval card says in one line when a command runs outside it. Version 1.6.3 with CHANGELOG. Tests first, then the whole suite and screen tests.
3. **Taskbar colours (REQ-28).** Built test-first in two tasks: (a) a small module that turns all projects' states into a colour and a tooltip (orange: a project waits for you or is ready for review; green: working, queued or waiting for the plan limit; grey: the rest), with unit tests for every case; (b) the Windows part: the dot on the taskbar button, flashing until you answer (paused while the window is in front, with a setting "Flash the taskbar until I answer", on by default), the same dot and tooltip on the tray icon when it is on, and a fallback to the normal icon if Windows refuses. Tested by unit tests and a screen check of the taskbar button (screenshots).
4. **Parked ideas (REQ-23 to REQ-27).** Nothing is built unless you ask.

## What you will try at the end
- Sandbox: a test command that writes to your Documents folder or reads a private folder is refused.
- Taskbar: start two projects, leave one waiting on an approval card. The dot turns orange and the button flashes. Answer the card and it turns green, then grey when all work ends.

## Risks and assumptions
- The sandbox may break Git credentials, pip's cache or claude.exe's sign-in: the investigation proves it first.
- Only a real run on your PC proves the taskbar behaviour. The taskbar may not be visible to the test tools, so part of the check is yours.
- REQ-1 to REQ-21 were not re-run by me.
- Uncommitted now: the .gitignore change and PROJECT_MAP.md.

## Iterations
| # | Iteration | Requirements | Status |
|---|---|---|---|
| 1 | **Before AI Team Lite: Start, projects and setup** - Setup, existing-folder import, layout, speed, spelling, voice (released up to 1.6.2) | REQ-1, REQ-13, REQ-16, REQ-20 | done |
| 2 | **Before AI Team Lite: Problem, requirements and architecture steps** - Problem card, devil's advocate, prior art, Mat, plan approval, council (released up to 1.6.2) | REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-18 | done |
| 3 | **Before AI Team Lite: Build loop and quality** - Test-first iterations, scorecard, checkpoints, diagrams (released up to 1.6.2) | REQ-7, REQ-8, REQ-15, REQ-17 | done |
| 4 | **Before AI Team Lite: Approvals, phone and keeping work moving** - Risk-based approvals, Telegram approvals, watchdog, pipeline, usage view (released up to 1.6.2) | REQ-9, REQ-10, REQ-11, REQ-12, REQ-14 | done |
| 5 | **Before AI Team Lite: Delivery and updates** - Windows installer, GitHub tests and releases, sharing and update check (released up to 1.6.2) | REQ-19, REQ-21 | done |
| 6 | **Before AI Team Lite: Start, projects and setup** - Setup, existing-folder import, layout, speed, spelling, voice (released up to 1.6.2) | REQ-1, REQ-13, REQ-16, REQ-20 | done |
| 7 | **Before AI Team Lite: Problem, requirements and architecture steps** - Problem card, devil's advocate, prior art, Mat, plan approval, council (released up to 1.6.2) | REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-18 | done |
| 8 | **Before AI Team Lite: Build loop and quality** - Test-first iterations, scorecard, checkpoints, diagrams (released up to 1.6.2) | REQ-7, REQ-8, REQ-15, REQ-17 | done |
| 9 | **Before AI Team Lite: Approvals, phone and keeping work moving** - Risk-based approvals, Telegram approvals, watchdog, pipeline, usage view (released up to 1.6.2) | REQ-9, REQ-10, REQ-11, REQ-12, REQ-14 | done |
| 10 | **Before AI Team Lite: Delivery and updates** - Windows installer, GitHub tests and releases, sharing and update check (released up to 1.6.2) | REQ-19, REQ-21 | done |
| 11 | **Sandbox investigation (L47, step 1)** - Report plus a prototype on this PC showing whether AppContainer works for Python, Git, pip, tests and claude.exe; a recommendation for you to decide | REQ-22 | planned |
| 12 | **Build the sandbox (1.6.3, only after your go)** - Engine and test runs start inside the chosen sandbox behind a setting, approval cards say when a command runs outside it | REQ-22 | planned |
| 13 | **Taskbar colours (CR-1)** - The taskbar icon shows an orange, green or grey dot for all projects, flashes until you answer, and the tray icon follows | REQ-28 | planned |
| 14 | **Parked ideas (not started)** - Keep the set-aside items on the list; nothing is built unless you ask | REQ-23, REQ-24, REQ-25, REQ-26, REQ-27 | planned |
| 15 | **Clean-up before the version** - remove dead code and duplication, refactor where needed, re-run every test | - | planned |
