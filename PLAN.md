## Goal
Fix the high-severity findings of the security review (reports/SECURITY_REVIEW.md) before version 1.6.3 is released, then clean up.

## What will be built
- **Security fixes (F-1 to F-5):** diagrams shown in a locked-down frame with no access to the app token; screenshots and test commands restricted to safe commands and refused before plan approval; Read/WebFetch refuse the secrets file, the sign-in folder and file:/// addresses; the sandbox's "not protected here" state is shown clearly instead of silently.
- **Medium fixes (F-6 to F-10):** approval state moved out of the project-writable file, push card shows the destination, phone cards show the command.
- Low findings (L-1 to L-7) are listed in the report; I will propose which to fix after the high ones.

## How each is tested
One automated test per finding, written first from the finding's attack (it must fail before the fix). The full suite and the screen checks run at the end. Screenshots: the diagram frame and the new approval card text.

## What you will try at the end
Open a diagram, start a build, see the push card with its destination.

## Risks
Stricter rules may block a command the team legitimately uses; if so the approval card asks you instead of failing silently.

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
| 18 | **Proof for the requirements** - Existing tests are mapped to REQ-1 to REQ-22 and recorded, so the Requirements tab shows what is proven and what is not | REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9, REQ-10, REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-16, REQ-17, REQ-18, REQ-19, REQ-20 | done (a680e49) |
|  | [x] Read the existing tests and map each to the requirement it really proves | tests/requirements_map.json | |
|  | [x] Run the mapped existing tests and record each result against its requirement | reports/test-logs | |
|  | [x] List the unproven requirements in STATUS.md | STATUS.md | |
| 19 | **One message draft per project** - Each project keeps its own unsent text and attachments, saved with the project | REQ-29 | done (c18b964) |
|  | [x] Server: save and load one draft per project (.aiteam/draft.json via safefile) | app/routes_work.py, app/server.py, app/projects.py | |
|  | [x] Screen: the message box keeps its own draft per project and restores it on switching | app/ui/js/composer.js, app/ui/js/start.js, app/ui/js/core.js | |
|  | [x] Document it | CHANGELOG.md, STATUS.md, README.md | |
| 20 | **Improvement ideas after each iteration** - A Yes/No table of up to 3 ideas after every iteration, with every decision saved and no idea shown twice | REQ-30 | done (97d5284) |
|  | [x] Decision log and report: .aiteam/ideas.jsonl, reports/IDEAS.md, no repeat of decided ideas | app/ideas.py, app/ledger.py | |
|  | [x] Team tool propose_ideas and the policy entry | app/tools_planning.py, app/policy.py, app/tools.py | |
|  | [x] Ideas card in the chat with Yes/No per row and an optional reason; Yes creates a change request, No is logged | app/session_approvals.py, app/routes_work.py, app/ui/js/chat.js, app/ui/app.css | |
|  | [x] Trigger after commit_iteration and Fred's instructions (Scout search, skip near the plan limit) | app/iterations.py, app/prompts.py, app/tools_building.py | |
|  | [x] Document it | CHANGELOG.md, STATUS.md, README.md | |
| 21 | **Taskbar colours** - The taskbar icon shows an orange, green or grey dot for all projects, flashes until you answer, and the tray icon follows | REQ-28 | done (28b35f3) |
|  | [x] Colour rules: all projects' states to orange, green or grey plus a tooltip (app/statusicon.py) | app/statusicon.py | |
|  | [x] Windows part: dot on the taskbar button, flashing until answered, tray follows, fallback to the normal icon | app/winhelpers.py, app/main.py, app/settings.py | |
|  | [x] Setting 'Flash the taskbar until I answer' in Settings > General | app/settings.py, app/routes_setup.py, app/server.py, app/ui/index.html, app/ui/js/screens.js | |
|  | [x] Document it | CHANGELOG.md, STATUS.md, README.md | |
| 22 | **Build the sandbox (1.6.3, only after your yes)** - Engine and test runs start inside the AppContainer behind a setting, approval cards say when a command runs outside it | REQ-22 | done (d24c101) |
|  | [x] AppContainer helper: create profile, grant/revoke folder access, start a process inside it (ctypes) | app/sandbox.py | |
|  | [x] Start the engine (claude.exe) inside the container from hide_engine_console, with the access list from ARCHITECTURE.md | app/winhelpers.py, app/paths.py | |
|  | [x] Same container for testrun.run and Launch the project | app/testrun.py, app/winhelpers.py | |
|  | [x] Setting to turn the sandbox on or off and one clear line on the approval card when a command runs outside it | app/routes_setup.py, app/session_approvals.py, app/ui/js/screens.js | |
|  | [x] Version 1.6.3: VERSION, CHANGELOG, README note | VERSION, CHANGELOG.md, README.md | |
| 23 | **Before AI Team Lite (iterations 1-22)** - Everything built up to the sandbox, committed | REQ-1, REQ-22 | done |
| 24 | **Parked ideas (kept parked)** - REQ-23 to REQ-27 stay on the list; nothing built, by your decision | REQ-23, REQ-24, REQ-25, REQ-26, REQ-27 | done |
| 25 | **Security fixes, high (F-1 to F-5)** - Close the five high findings with a failing-first test each | REQ-1, REQ-22 | done (60f6e33) |
|  | [x] F-5: sandbox the diagram frame, no token access | app/server.py, app/ui/js/side.js, app/ui/index.html | |
|  | [x] F-1: screenshot window command checked/approved, no shell=True | app/policy.py, app/tools_building.py, app/winhelpers.py | |
|  | [x] F-2: run_tests/commit_iteration command allowlist, refused before plan approval | app/policy.py, app/tools_building.py, app/iterations.py | |
|  | [x] F-4: Read/Grep/WebFetch refuse secrets, sign-in folder, file:/// and --flags | app/policy.py, app/auth.py, app/tools_building.py | |
|  | [x] F-3: show clearly when the sandbox is not protecting the engine | app/sandbox.py, app/sandbox_launch.py | |
| 26 | **Security fixes, medium (F-6 to F-10)** - Close the five medium findings with tests | REQ-1, REQ-22 | done (fec8292) |
|  | [x] F-6: keep plan_approved and Launch command out of the project-writable file | app/projects.py, app/policy.py | |
|  | [x] F-7: git push card shows destination; refuse unexpected origin / planted git config | app/github.py | |
|  | [x] F-9/F-10: phone approval cards show the command or file; separate temp folders for engine and test containers | app/telegram.py, app/sandbox.py | |
| 27 | **Clean-up before the version** - remove dead code and duplication, refactor where needed, re-run every test | - | building |
|  | [ ] Whole-codebase clean-up, no behaviour change | app/ | |
