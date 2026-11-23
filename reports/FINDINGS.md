# Review findings

> Kept by AI Team Lite: every finding of a review stays here until it is fixed, or accepted with a reason.

| # | Finding | Severity | Where | Status |
|---|---------|----------|-------|--------|
| F-1 | H-1: take_screenshot kind=window runs any command with shell=True, unboxed, no approval | high | policy.py:39, tools_building.py:151, winhelpers.py:317 | fixed (No shell; only a plain app start is auto-allowed; tests in test_sec_screenshot.py (commit 60f6e33)) |
| F-2 | H-2: run_tests/commit_iteration accept any command passing a short word list, allowed before plan approval | high | policy.py:34, tools_building.py:89, iterations.py:285 | fixed (Test-command allowlist, refused before plan approval, sandbox refusal; tests in test_sec_tests_cmd.py (60f6e33)) |
| F-3 | H-3: sandbox fails open (engine and its shell run unboxed when Git Bash won't start in AppContainer) | high | sandbox.py:396-449, sandbox_launch.py:33 | open |
| F-4 | H-4: Read/Glob/Grep/WebFetch always allowed; secrets file, sign-in folder and API key reachable; screenshot_url takes file:/// and --flags | high | policy.py:21-22, auth.py:43 | open |
| F-5 | H-5: /diagram HTML served on app origin in unsandboxed iframe; script can read token and approve cards | high | server.py:248, side.js:109, index.html:112 | fixed (Diagram frame sandboxed with CSP, one-time ticket bound to the diagram; tests in test_sec_diagram.py (60f6e33)) |
| F-6 | M-1: plan_approved and Launch command stored in writable .aiteam/project.json | medium | .aiteam/project.json handling | fixed (State moved to app data folder; tests in test_sec_state.py (fec8292)) |
| F-7 | M-2: git runs unboxed in project folders; planted .git/config or changed origin gets GitHub token; push card hides destination | medium | github.py:97-150 | fixed (Origin and push-URL checks, hardened git, destination on card; test_sec_git.py (fec8292)) |
| F-8 | M-3: shell policy is a word list, easy to get around | medium | policy.py | open |
| F-9 | M-4: engine and test/Launch containers share one writable temp folder (sitecustomize.py planting) | medium | sandbox.py | fixed (Phone card shows masked command; test_sec_phone_temp.py (fec8292)) |
| F-10 | M-5: phone approval cards show no command or file | medium | Telegram bridge approval cards | fixed (Separate engine temp folder, py-site read-only; checked by tests only, not in a real container (fec8292)) |
