> Rebuilt by Fred from the existing files - please check.

# Status: AI Team Lite

## What it is
A Windows desktop app where Fred, an AI team lead, helps non-developers turn a business need into a tested
prototype: problem first, requirements, architecture, plan, iterations built test-first, a real installer at the end.
Public repo: github.com/XaeriS/ai-team-lite (MIT). Version 1.6.2.

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
- REQ-28 taskbar colours: planned after the sandbox.
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
