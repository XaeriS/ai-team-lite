# Quality of this version

> Made by AI Team Lite on 2026-11-23 01:36 after iteration 27 (f25127a).

**Good to go, with 2 points to look at.**

| Check | Result | In short |
|---|---|---|
| Every requirement proven by a test | To look at | 21 of 30 built requirements proven by a test; not yet: REQ-4, REQ-11, REQ-14, REQ-19, REQ-23, REQ-24, REQ-25, REQ-26. |
| Tests | OK | 28 tests passing, 0 failing; last full run passed. |
| Code check | OK | 143 Python files checked: no issues; 5 long files. |
| Packages the app uses | To look at | 1 of 9 packages has a fixed version; no known security problems in 5 packages. |
| No secrets in the files | OK | No keys or passwords found in 258 files. |
| Review findings | OK | 0 open, 7 fixed, 3 accepted by you. |
| Documents up to date | OK | STATUS.md (and the README, once the app runs) follow the latest work. |

## Every requirement proven by a test

- REQ-4 is built but no passing test proves it yet: (existing) Prior-art check. When the option is on, then the Scout searches the web and a card lists existing tools with links (reports/PRIORART.md).
- REQ-11 is built but no passing test proves it yet: (existing) Work keeps moving. When nothing moves for 3 minutes Fred says what runs; after 5 minutes the app wakes Fred, then restarts the step, then asks the user; never while a question waits. When t
- REQ-14 is built but no passing test proves it yet: (existing) Usage and cost. When the user clicks the usage meter, then they see cost per iteration, day, week, month, agent and phase, time per phase, and remaining Claude plan.
- REQ-19 is built but no passing test proves it yet: (existing) Delivery. When a project is finished, then Fred can package a Windows installer (Start menu, icon, optional desktop shortcut, uninstaller, no admin rights), add GitHub tests and publish a r
- REQ-23 is built but no passing test proves it yet: (later) Parallel build of independent tasks in separate Git worktrees (L50).
- REQ-24 is built but no passing test proves it yet: (later) Handover pack for IT (L52a) and signing the installer (L52e).
- REQ-25 is built but no passing test proves it yet: (later) Portfolio view across projects (L55).
- REQ-26 is built but no passing test proves it yet: (later) Reusable templates (L56).
- REQ-27 is built but no passing test proves it yet: (later) A second person can review or approve (L57).

## Code check

- app/projects.py has 561 lines (easier to maintain when split, below 500)
- app/sandbox.py has 510 lines (easier to maintain when split, below 500)
- tests/demo_server.py has 504 lines (easier to maintain when split, below 500)
- tests/test_v100.py has 1577 lines (easier to maintain when split, below 500)
- tests/test_v11.py has 595 lines (easier to maintain when split, below 500)

## Packages the app uses

- pywebview has no fixed version (>=6.2,<7): a new release could change the app without anyone noticing; it is not installed yet either, so it could not be checked for known problems
- keyring has no fixed version (>=25): a new release could change the app without anyone noticing
- pillow has no fixed version (>=11): a new release could change the app without anyone noticing
- faster-whisper has no fixed version (>=1.2,<2): a new release could change the app without anyone noticing; it is not installed yet either, so it could not be checked for known problems
- sounddevice has no fixed version (>=0.5): a new release could change the app without anyone noticing; it is not installed yet either, so it could not be checked for known problems
- pystray has no fixed version (>=0.19.5): a new release could change the app without anyone noticing; it is not installed yet either, so it could not be checked for known problems
- pyspellchecker has no fixed version (>=0.8,<1): a new release could change the app without anyone noticing
- pyflakes has no fixed version (>=3.2,<5): a new release could change the app without anyone noticing

## Review findings

- F-3 accepted by you: H-3: sandbox fails open (engine and its shell run unboxed when Git Bash won't start in AppContainer) (reason: User accepted for 1.6.3: engine shell stays unboxed (Git Bash does not start in the box); visible "Not protected" warning. Real fix needs a separate investigation.)
- F-4 accepted by you: H-4: Read/Glob/Grep/WebFetch always allowed; secrets file, sign-in folder and API key reachable; screenshot_url takes file:/// and --flags (reason: User accepted for 1.6.3: Read/Glob/Grep/WebFetch/screenshots guarded; shell route and API key in engine environment remain, depend on boxing the shell (F-3).)
- F-8 accepted by you: M-3: shell policy is a word list, easy to get around (reason: User accepted for 1.6.3: shell rules are a guide, the box is the real wall (see F-3).)
