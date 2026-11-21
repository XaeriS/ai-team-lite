# Plan

## Goal
Carry on from AI Team Lite 1.6.2 (released, nothing rebuilt). Next: a sandbox so a bad package or a tricked agent cannot write outside the project or read the user's private files (REQ-22). Overview diagram: `.archify/architecture-ai-team-lite-20261121-085038/architecture-ai-team-lite.html` (10 existing parts, the sandbox drawn as "planned").

## Already done (shown as done, not rebuilt)
Setup, problem-first questions, devil's advocate, prior art, Mat's architecture step, plan approval, test-first build loop, scorecard, risk-based approvals, phone approvals, watchdog, pipeline, voice and spelling, usage, checkpoints, existing-folder import, diagrams, council, delivery and installer, layout and speed, updates (REQ-1 to REQ-21), grouped into five finished iterations below.

## Still to do
1. **Sandbox investigation (no release, no change to the app).** Write `reports/L47-sandbox-options.md`: what the AppContainer protects (writing elsewhere, reading private files, network), what it needs from you, and a prototype on this Windows 11 Home PC that proves Python, Git, pip, the tests and claude.exe work inside it. If AppContainer breaks them, the restricted-token option is tried. It ends with one recommendation and a question to you. Tested by: a script that runs the prototype and records pass/fail per tool (screenshots of the results).
2. **Build the sandbox (only after you say "go" on the report).** Behind a setting; the engine and test runs start inside it; the approval card says in one line when a command runs outside it; policy.py stays as the second guard. Version 1.6.3, with CHANGELOG. Tests first, whole suite plus screen tests, then the usual quality check. No push to GitHub without your approval.
3. **Parked ideas (REQ-23 to REQ-27).** Parallel build, IT handover pack and signing, portfolio view, templates, second reviewer. You set these aside on 2026-11-17. Nothing is built; the iteration only keeps them on the list.

## What you will try at the end
Start a project with the sandbox on: the team works normally, and a test command that writes to your Documents folder or reads a private folder is refused.

## Risks and assumptions
- Git credentials, pip's cache and claude.exe's sign-in may not work inside the container: that is what step 1 proves.
- Only a real run on your PC proves it; I cannot prove it elsewhere.
- Untested assumption: features REQ-1 to REQ-21 work as the README says; I did not re-run them.
- Uncommitted now: the .gitignore change and PROJECT_MAP.md.

## Iterations
| # | Iteration | Requirements | Status |
|---|---|---|---|
| 1 | **Before AI Team Lite: Start, projects and setup** - Setup, existing-folder import, layout, speed, spelling, voice (released up to 1.6.2) | REQ-1, REQ-13, REQ-16, REQ-20 | done |
| 2 | **Before AI Team Lite: Problem, requirements and architecture steps** - Problem card, devil's advocate, prior art, Mat, plan approval, council (released up to 1.6.2) | REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-18 | done |
| 3 | **Before AI Team Lite: Build loop and quality** - Test-first iterations, scorecard, checkpoints, diagrams (released up to 1.6.2) | REQ-7, REQ-8, REQ-15, REQ-17 | done |
| 4 | **Before AI Team Lite: Approvals, phone and keeping work moving** - Risk-based approvals, Telegram approvals, watchdog, pipeline, usage view (released up to 1.6.2) | REQ-9, REQ-10, REQ-11, REQ-12, REQ-14 | done |
| 5 | **Before AI Team Lite: Delivery and updates** - Windows installer, GitHub tests and releases, sharing and update check (released up to 1.6.2) | REQ-19, REQ-21 | done |
| 6 | **Sandbox investigation (L47, step 1)** - Report plus a prototype on this PC showing whether AppContainer works for Python, Git, pip, tests and claude.exe; a recommendation for you to decide | REQ-22 | planned |
| 7 | **Build the sandbox (1.6.3, only after your go)** - Engine and test runs start inside the chosen sandbox behind a setting, approval cards say when a command runs outside it | REQ-22 | planned |
| 8 | **Parked ideas (not started)** - Keep the set-aside items on the list; nothing is built unless you ask | REQ-23, REQ-24, REQ-25, REQ-26, REQ-27 | planned |
| 9 | **Clean-up before the version** - remove dead code and duplication, refactor where needed, re-run every test | - | planned |
