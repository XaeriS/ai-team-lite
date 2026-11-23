> Rebuilt by Fred from the existing files - please check.

# Status: AI Team Lite

## What it is
A Windows desktop app where Fred, an AI team lead, helps non-developers turn a business need into a tested
prototype: problem first, requirements, architecture, plan, iterations built test-first, a real installer at the end.
Public repo: github.com/XaeriS/ai-team-lite (MIT). Version 1.6.3 (file protection added: test runs and "Launch the project" run in a Windows AppContainer; the engine's own shell commands still run outside it, and the approval card says so).

## Technologies
Python (about 12,000 lines in app/), Claude Agent SDK 0.2.165, pywebview (Edge WebView2) window, local Python
server with a token and server-sent events, plain HTML/CSS/JS screens, keyring, faster-whisper (voice),
pyspellchecker, pyflakes, NSIS installer, GitHub Actions (tests, release).

## How to run
- Users: install from the GitHub Releases page (unsigned, Windows warns once).
- Developer: `pip install -r requirements-dev.txt pyflakes playwright`, then `python -m pytest -q`, each
  `python tests/ui_*.py <screenshot folder>`, and `python -m pyflakes app tests`. Start with `python -m app.main`
  (taken from app/main.py; not run by me).

## What works (per README, CHANGELOG and tests; not re-run by me)
Setup, problem card and challenge, devil's advocate, prior art, Mat's architecture step, iteration plan and
approval, test-first build loop, scorecard, risk-based approvals, phone approvals, watchdog and plan-limit
restart, pipeline, voice, spelling, usage view, checkpoints, existing-folder import, diagrams, council,
installer packaging and GitHub release.

## Unfinished or broken
- L47 sandbox: investigation done (iteration 11, 2026-11-21): reports/L47-sandbox-options.md and tools/sandbox_probe.py. AppContainer passed Python, Git, pip and claude.exe and refused writing outside and reading a canary file; pytest needs read-attributes on the parent folder. Not built yet: waits for the owner's decision. Still unproven: Claude's sign-in, git push, pip install online, apps with a window.
- REQ-28 taskbar colours: built (app/statusicon.py rules, app/taskbar_win.py overlay dot and flashing, tray dot, setting "Flash the taskbar until I answer"); proven by tests/test_l73_statusicon.py and tests/ui_l73.py; the look on the real taskbar still needs the owner's eye.
- REQ-30 improvement ideas after each iteration: built (app/ideas.py, propose_ideas, ideas card in chat.js, reports/IDEAS.md); proven by tests/ui_l72.py; listed under Unreleased in CHANGELOG.md.
- REQ-29 one message draft per project: built (GET/PUT /api/projects/{slug}/draft, .aiteam/draft.json, composer.js); listed under Unreleased in CHANGELOG.md.
- Today the only guard against a harmful command is text patterns (policy.py, risk.py).
- The installer is unsigned.
- Set aside: parallel build, IT handover pack, signing, portfolio view, templates, second reviewer.

## Recent history
1.6.2 (fewer approvals, fast long chats, Word-style spelling, real installers) is the last commit.
Before it: 1.6.1 (odd folder names), 1.6.0 (quality scorecard), 1.5.x (checkpoints, code split), 1.4.0 (small
windows, spelling), 1.3.x (watchdog and restarts), 1.0.0 to 1.1.0 (first Lite release, phone approvals).
Uncommitted: .gitignore adds `.aiteam/`; PROJECT_MAP.md is new.

## Risks
- Agents run commands with the user's rights; only text patterns guard them (README says "Prototype").
- Every push to main publishes a public version.
- Features were not re-run in this scan.

## Candidate next steps
1. L47 investigation report (1.6.3), then the owner's decision.
2. Commit the .gitignore change and PROJECT_MAP.md.
3. Pick from the set-aside items only if the owner's need changes.

## Requirements not proven by a test

- REQ-4: No test checks the Scout's web search, the prior-art card or reports/PRIOR_ART.md.

- Parked, not built (decision 2026-11-22: keep parked): REQ-23 parallel build in Git worktrees, REQ-24 IT handover pack and installer signing, REQ-25 portfolio view, REQ-26 reusable templates, REQ-27 second reviewer or approver. Nothing in the app depends on them.

- Security fixes, high (iteration 25): F-1 window screenshots, F-2 test commands, F-5 diagram frame fixed; F-4 (private files, web addresses) fixed for Read/Glob/Grep/WebFetch/screenshots but the shell route and the API key in the engine environment remain open; F-3 shows a "Not protected" warning but the engine still starts unboxed when the box cannot start. F-6 to F-10 (medium) still to do. See reports/SECURITY_REVIEW.md.

- Security fixes, medium (iteration 26): F-6 approval state kept outside the project folder (old projects keep plan/architecture approval once; Launch command must be saved again), F-7 origin and push-URL checks plus hardened git, F-9 phone cards show a masked command, F-10 separate temp folders for the engine and the test/Launch boxes. The new box layout is checked by tests only, not yet in a real Windows container: try "Launch the project" and a test run once. F-8 (word-list shell policy) and F-3/F-4 remainders stay open.

- Clean-up (iteration 27): removed unused is_readonly in policy.py; no behaviour change; 548 tests pass.
