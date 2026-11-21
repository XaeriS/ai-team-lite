# Plan

## Goal
Carry on from AI Team Lite 1.6.2 (released, nothing rebuilt). The sandbox investigation (iteration 11) is finished and saved. Remaining work, with the new requests you approved (CR-1, CR-2, CR-3):

## Already done (not rebuilt)
REQ-1 to REQ-21 (released up to 1.6.2) and the sandbox investigation (REQ-22, step 1).

## Still to do, in this order
1. **Proof for the requirements.** The Requirements tab shows "proven by test" only for tests recorded against a requirement. The Tester maps the existing test files to REQ-1 to REQ-22, records each result, and reports which requirements have no test (they stay "not proven" and I list them for you). No change to the app.
2. **One message draft per project (REQ-29).** Each project keeps its own unsent text and attachments, saved with the project so a restart does not lose them. Small.
3. **Improvement ideas after each iteration (REQ-30).** A table card with Yes and No per idea (at most 3, from Fred and a short web search by the Scout), every decision saved in `reports/IDEAS.md` and a log, no idea shown twice. Medium.
4. **Taskbar colours (REQ-28).** Orange, green or grey dot for all projects, flashing until you answer (paused when the window is in front, with a setting), tray follows. Medium.
5. **Build the sandbox (REQ-22, version 1.6.3), only after your yes** to Mat's question: it protects your files outside the project from being changed or read, not the network or the project folder, and needs your yes once. Large, and the one that uses most of your plan.
6. **Parked ideas (REQ-23 to REQ-27).** Nothing is built unless you ask.

I moved the sandbox build to fifth place: it still waits for your yes, and it is the biggest step for your plan usage (95% this session, reset at 1:30 pm Phoenix time). Say so if you want it earlier.

## How each is tested
Tests are written first and must fail, then the code, then the whole suite and a screen test with screenshots. Version number, CHANGELOG and the GitHub push wait for your approval.

## What you will try at the end
- Drafts: type in project A, switch to B (empty box), back to A (text back), restart the app (still there).
- Ideas: finish an iteration, get the table, click Yes on one and No on another, open `reports/IDEAS.md` and find both with the date.
- Taskbar: leave one project waiting on an approval card: orange dot and flashing; answer it: green, then grey.

## Risks and assumptions
- The web search after every iteration costs plan usage; it is skipped when the limit is near.
- The taskbar dot can only be fully proven on your PC.
- REQ-1 to REQ-21 were never re-run by me; step 1 shows what the existing tests prove.
- Uncommitted now: the .gitignore change, PROJECT_MAP.md and the documents I updated.

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
| 11 | **Sandbox investigation (L47, step 1)** - Report plus a prototype on this PC showing whether AppContainer works for Python, Git, pip, tests and claude.exe; a recommendation for you to decide | REQ-22 | done (621aa62) |
|  | [x] Probe script: start programs inside an AppContainer and record pass/fail | tools/sandbox_probe.py | |
|  | [x] Run the probe on this PC for AppContainer (B) and, if needed, restricted token (A) | reports/L47-probe-results.json | |
|  | [x] Write the report with one recommendation and the question for the owner | reports/L47-sandbox-options.md | |
| 12 | **Before AI Team Lite: Start, projects and setup** - Setup, existing-folder import, layout, speed, spelling, voice (released up to 1.6.2) | REQ-1, REQ-13, REQ-16, REQ-20 | done |
| 13 | **Before AI Team Lite: Problem, requirements and architecture steps** - Problem card, devil's advocate, prior art, Mat, plan approval, council (released up to 1.6.2) | REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-18 | done |
| 14 | **Before AI Team Lite: Build loop and quality** - Test-first iterations, scorecard, checkpoints, diagrams (released up to 1.6.2) | REQ-7, REQ-8, REQ-15, REQ-17 | done |
| 15 | **Before AI Team Lite: Approvals, phone and keeping work moving** - Risk-based approvals, Telegram approvals, watchdog, pipeline, usage view (released up to 1.6.2) | REQ-9, REQ-10, REQ-11, REQ-12, REQ-14 | done |
| 16 | **Before AI Team Lite: Delivery and updates** - Windows installer, GitHub tests and releases, sharing and update check (released up to 1.6.2) | REQ-19, REQ-21 | done |
| 17 | **Sandbox investigation (L47, step 1)** - Report plus a prototype on this PC; finished and committed (621aa62) | REQ-22 | done |
| 18 | **Proof for the requirements** - Existing tests are mapped to REQ-1 to REQ-22 and recorded, so the Requirements tab shows what is proven and what is not | REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9, REQ-10, REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-16, REQ-17, REQ-18, REQ-19, REQ-20 | building |
|  | [ ] Read the existing tests and map each to the requirement it really proves | tests/requirements_map.json | |
|  | [ ] Run the mapped existing tests and record each result against its requirement | reports/test-logs | |
|  | [ ] List the unproven requirements in STATUS.md | STATUS.md | |
| 19 | **One message draft per project** - Each project keeps its own unsent text and attachments, saved with the project | REQ-29 | planned |
| 20 | **Improvement ideas after each iteration** - A Yes/No table of up to 3 ideas after every iteration, with every decision saved and no idea shown twice | REQ-30 | planned |
| 21 | **Taskbar colours** - The taskbar icon shows an orange, green or grey dot for all projects, flashes until you answer, and the tray icon follows | REQ-28 | planned |
| 22 | **Build the sandbox (1.6.3, only after your yes)** - Engine and test runs start inside the AppContainer behind a setting, approval cards say when a command runs outside it | REQ-22 | planned |
| 23 | **Parked ideas (not started)** - Keep the set-aside items on the list; nothing is built unless you ask | REQ-23, REQ-24, REQ-25, REQ-26, REQ-27 | planned |
| 24 | **Clean-up before the version** - remove dead code and duplication, refactor where needed, re-run every test | - | planned |
