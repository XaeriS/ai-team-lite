# Project map

> Maintained automatically by AI Team Lite after each iteration. Read this first to find the right file;
> open only the files you need.

**Tests:** 34 file(s) in `reports`, `tests`

| File | What it is for |
|------|----------------|
| `.github/workflows/release.yml` | data / settings file |
| `.github/workflows/tests.yml` | data / settings file |
| `app/__init__.py` | AI Team Lite: a desktop app where Fred (a Claude agent) turns your ideas into working software. |
| `app/analytics.py` | ---------------------------------------------------------------------------- periods (L9) |
| `app/auth.py` | How Fred connects to Claude: with an Anthropic API key, or with the user's own Claude subscription. |
| `app/brains.py` | Free vs paid per model (best-effort, by name). Used for the badge on each model (item 40). |
| `app/challenge.py` | L5: why each question matters, in plain words (what goes wrong when it is skipped) |
| `app/checkpoints.py` |  |
| `app/council.py` | anonymous peer review: shuffle, label A, B, C... |
| `app/diagrams.py` | 'How it works' diagrams of a project (item 78), drawn with Archify by the team's diagrammer. |
| `app/engine.py` | reached through this module by the server and the tests |
| `app/engine_const.py` | Timings and names shared by the engine's files (L46). Tests change some of them here. |
| `app/engine_limits.py` | Plan limits, an expired sign-in and an engine closed by Windows: wait, then continue on a fresh start (L25, L30, L37). |
| `app/engine_pipeline.py` | The master plan across projects: order, what runs next, release, pause and resume, checkpoints, overviews. |
| `app/engine_text.py` | Plain helpers of the engine: how a tool call, an approval or an error is put into words, what starts a |
| `app/engine_watchdog.py` |  |
| `app/events.py` | Event hub: everything the chat shows is an event. Events are pushed live to the window (SSE) and the |
| `app/features.py` | Requested features and feedback (items 50, 51). |
| `app/freshtests.py` | L64: after an iteration passes its full test run and is saved, the test results show that run, not the history. |
| `app/github.py` |  |
| `app/ideas.py` | REQ-30: improvement ideas after each iteration, and what the user decided about each. |
| `app/iterations.py` | Iteration-based delivery: the plan is split into iterations (one feature each). Nothing is built before |
| `app/journal.py` | Run journal and recovery (item 33). |
| `app/languages.py` | How the team builds: Python with the bundled interpreter (AI Team Lite builds in Python only). |
| `app/ledger.py` | Append-only JSON-lines logs shared by the token log (usage) and the time log (timeline). |
| `app/lessons.py` | Lessons learned across projects (L23). |
| `app/main.py` |  |
| `app/models.py` | Claude models for the team, who uses which, and the price list used for cost estimates. |
| `app/packaging.py` |  |
| `app/paths.py` | Where things live: the install folder (read-only app files) and the data folder (your settings). |
| `app/policy.py` | helpers that only read, search the web or draw diagrams: they may start before a plan is approved |
| `app/projectmap.py` | PROJECT_MAP.md: a short, automatically maintained index of the project's files (L20). |
| `app/projects.py` | ---------------------------------------------------------------------------- existing folders |
| `app/prompts.py` | # How you talk |
| `app/reset.py` | Start AI Team Lite fresh: erase its settings, saved keys, Claude sign-in, setup chat, logs and window data. |
| `app/risk.py` | (pattern, what it does, risk, why) |
| `app/routes_projects.py` |  |
| `app/routes_setup.py` |  |
| `app/routes_work.py` | the real session / week figures from Claude's own usage command (item 69); no tokens used |
| `app/safefile.py` | Saving small JSON files safely from several threads (L34). |
| `app/sandbox.py` | ------------------------------------------------------------------ setting and availability |
| `app/sandbox_launch.py` | REQ-22: starts Claude's engine (claude.exe) inside the project's AppContainer. |
| `app/sandbox_win.py` | internetClient, internetClientServer, privateNetworkClientServer: the network stays open (ARCHITECTURE.md) |
| `app/scorecard.py` | ---------------------------------------------------------------------------- the checks |
| `app/secrets_store.py` | API keys and tokens live in Windows Credential Manager (via keyring), never in plain files or prompts. |
| `app/server.py` | ---------------------------------------------------------------- voice |
| `app/server_common.py` |  |
| `app/session.py` | One project's conversation with Claude: the team's state, approvals, a turn, background workers and the |
| `app/session_approvals.py` | while this project waits for the user, its working slot goes to the next work in the master plan |
| `app/session_background.py` |  |
| `app/session_messages.py` | What Claude sends back, turned into what the user sees: messages, tool steps, workers, results, errors. |
| `app/settings.py` | App settings, stored as JSON in the data folder. Secrets are NOT stored here (see secrets_store). |
| `app/setup_extras.py` | Run by the installer after the files are copied (item 70): set up the optional extras the user ticked, and |
| `app/shellcheck.py` | commands that change files or folders (anything else here only reads) |
| `app/shipping.py` | L52c: automatic tests on GitHub for a project the team built (GitHub Actions, on Windows like the user's PC). |
| `app/skills/impeccable/NOTICE.md` | Third-Party Notices |
| `app/skills/impeccable/reference/craft-floor.md` | Craft floor |
| `app/skills/impeccable/reference/operate.md` | Operate mode depth (and Read notes) |
| `app/skills/impeccable/reference/polish.md` | 1. Establish the system |
| `app/skills/impeccable/SKILL.md` | Setup |
| `app/skills/no-ai-slop/eval.md` | No AI slop eval |
| `app/skills/no-ai-slop/SKILL.md` | No AI slop |
| `app/skills/ponytail/SKILL.md` | Ponytail |
| `app/skills/security-audit/ATTACK-CLASSES.md` | Attack Classes |
| `app/skills/security-audit/SKILL.md` | Security Audit |
| `app/skills/SOURCES.md` | Bundled skills (reviewed copies) |
| `app/skills.py` | Community skills the team may use (reviewed copies in app/skills, see SOURCES.md). |
| `app/spelling.py` | Spelling help for what the user types to Fred (L38). Words are checked against the dictionaries of the chosen |
| `app/statusicon.py` | REQ-28: one colour for the taskbar / tray icon from the states of all projects. |
| `app/taskbar_win.py` | ---------------------------------------------------------------- window |
| `app/telegram.py` | ---------------------------------------------------------------------------- what a card looks like on the phone |
| `app/testrun.py` | L32: out of sight, on a desktop the user never sees; the normal way if Windows does not allow it |
| `app/timeline.py` | ---------------------------------------------------------------------------- recording |
| `app/tooling.py` | Programs the team may need on the PC: Ollama (local brains for the council) and Archify (diagrams). |
| `app/tools.py` | Tools that Fred and the workers can call besides the built-in ones (files, shell, web). |
| `app/tools_building.py` |  |
| `app/tools_common.py` | Small helpers and texts shared by the team's tools: answers, test results, diagrams, decisions (L46). |
| `app/tools_delivery.py` | Tools for delivering a finished project (L52b-d): a real Windows installer, automatic tests on GitHub, and a |
| `app/tools_planning.py` |  |
| `app/tools_setup.py` |  |
| `app/tracking.py` | ---------------------------------------------------------------------------- requirements status |
| `app/ui/app.css` |  |
| `app/ui/index.html` | AI Team Lite |
| `app/ui/js/chat.js` |  |
| `app/ui/js/checkpoints.js` |  |
| `app/ui/js/clipboard.js` |  |
| `app/ui/js/composer.js` |  |
| `app/ui/js/core.js` |  |
| `app/ui/js/scorecard.js` |  |
| `app/ui/js/screens.js` |  |
| `app/ui/js/side.js` |  |
| `app/ui/js/spelling.js` |  |
| `app/ui/js/start.js` |  |
| `app/ui/js/usage.js` |  |
| `app/updates.py` | Publish AI Team Lite's own source to a GitHub repo to share it, and check that repo for a newer version so the |
| `app/upkeep.py` | ---------------------------------------------------------------------------- L41: documents follow the code |
| `app/usage.py` | a real figure from Claude's usage command is fresher than a status report without a percentage, but the |
| `app/voice.py` | Hands-free talking: AI Team Lite listens through the microphone, turns speech into text on this PC |
| `app/winhelpers.py` | Windows helpers: voice typing, screenshots, launching programs. Each has a fallback for other systems |
| `ARCHITECTURE.md` | how it is built (approved decisions) |
| `CHANGELOG.md` | What changed |
| `PLAN.md` | the plan and its iterations |
| `README.md` | how to use the app |
| `reports/L47-probe-results.json` | data / settings file |
| `reports/L47-sandbox-options.md` | L47: keeping the team's commands in a box (REQ-22) |
| `reports/test_results.json` | data / settings file |
| `requirements-dev.txt` |  |
| `REQUIREMENTS.md` | the agreed requirements (REQ-n) |
| `requirements.txt` | Python packages the app needs |
| `STATUS.md` | where the project stands |
| `tests/conftest.py` | defines isolated |
| `tests/demo_server.py` | project conversation |
| `tests/fakeclaude.py` | A stand-in for ClaudeSDKClient that plays scripted turns, including real calls into the app's |
| `tests/fixtures_graph.html` | graphify - graphify-out/graph.html |
| `tests/requirements_map.json` | data / settings file |
| `tests/test_auth_reset.py` | defines test_cli_env_modes, test_ready_per_mode, test_status_parses_cli, test_engine_uses_subscription_env, test_reset_erases_app_data_but_not_projects |
| `tests/test_checkpoints.py` | undo: everything is back, including the work that had not been saved |
| `tests/test_core.py` | defines decide, test_policy_reading_always_allowed, test_policy_docs_allowed_code_needs_plan, test_policy_shell, test_policy_special_tools, test_project_create_and_inside … |
| `tests/test_iterations.py` | replanning keeps the finished iteration and numbers continue |
| `tests/test_l47_probe.py` | ---- saved results of the real run on this PC ---- |
| `tests/test_l52.py` |  |
| `tests/test_l58.py` | L58: one folder whose name is not a project id (capitals, spaces) emptied the whole app: no projects, no setup |
| `tests/test_l59.py` | L59: opening a project with a long chat was slow (2 s on the owner's PC): the whole history was sent and |
| `tests/test_l64.py` | L64: once an iteration passes its full test run and is saved, its results replace the older ones. |
| `tests/test_l65.py` |  |
| `tests/test_l67.py` | L67: "JSON message exceeded maximum buffer size of 1048576 bytes" stopped a new project (the team read a big |
| `tests/test_l68_l69.py` | L69: the plan comes before the diagram, and the user decides whether the diagrammer starts. |
| `tests/test_l70.py` | L70: the whole-codebase clean-up comes after every code change of a version, just before the GitHub push. |
| `tests/test_l71_drafts.py` |  |
| `tests/test_l72_ideas.py` | REQ-30 (L72, CR-3): improvement ideas after each iteration. Written BEFORE the code (app/ideas.py, |
| `tests/test_l73_statusicon.py` | ------------------------------------------------------------------ colour |
| `tests/test_lite.py` | AI Team Lite: a separate app from AI Team (own data, keys, sign-in, install, instance) with the expert features removed. |
| `tests/test_sandbox.py` | REQ-22: the team's commands run inside a Windows AppContainer (the "sandbox"). Written BEFORE the code |
| `tests/test_sandbox_engine.py` |  |
| `tests/test_sandbox_setting.py` | REQ-22: the setting that turns the sandbox on or off. Written BEFORE the code. |
| `tests/test_scorecard.py` | the key itself never appears in what is saved or shown |
| `tests/test_server.py` | projects cannot be created before setup is finished |
| `tests/test_traceability.py` | Traceability: every requirement (REQ-1..REQ-21) is mapped to tests, or listed as unproven with a reason. |
| `tests/test_v04.py` | ------------------------------------------------------------------------------- a stand-in brain (OpenAI-compatible) |
| `tests/test_v06.py` | 0.6.0: Mat the lead architect, projects from an existing folder, hide/delete projects, idle state. |
| `tests/test_v07.py` | ------------------------------------------------------------------------------- models (items 8-10) |
| `tests/test_v08.py` | ------------------------------------------------------------------------------- languages (35) |
| `tests/test_v09.py` | 0.9.0: brain test-before-add + tiers, usage in/out split + cache savings, context cap, changes diff, updates. |
| `tests/test_v10.py` | 0.10.0: in-app feedback -> feature queue (50), requested-features page (51), owner/friend sharing (49), |
| `tests/test_v100.py` | ------------------------------------------------------------------------------- L7 |
| `tests/test_v11.py` | ------------------------------------------------------------------ (54) Gemini list |
| `tests/test_v14.py` |  |
| `tests/test_voice_usage.py` | ------------------------------------------------------------------------------- end word |
| `tests/ui_checkpoints.py` | Browser test of L48: the "What changed" window of an iteration (plain words, requirement, files by kind, the lines |
| `tests/ui_e2e.py` | 1. onboarding: API key |
| `tests/ui_files_times.py` | Browser test: delete attachments before/after sending; send and reply times on messages. |
| `tests/ui_l58.py` | Browser test of L58: an odd folder name no longer empties the window; if loading ever fails, the window says so. |
| `tests/ui_l59.py` | Browser test of L59: a long chat opens with its latest part; earlier messages come back on request. |
| `tests/ui_l71.py` | Browser test of REQ-29 (L71): each project keeps its own unsent message and attachments, saved with the project. |
| `tests/ui_l72.py` | Browser test of REQ-30 (L72): the improvement-ideas card, decisions shown, kept after reload, saved in reports/IDEAS.md. |
| `tests/ui_l73.py` | Browser test of REQ-28 (L73): the "Flash the taskbar until I answer" setting in Settings > General. |
| `tests/ui_lite.py` | Browser test of AI Team Lite's screens: one simple layout (no Advanced switch), no expert tabs or controls, |
| `tests/ui_scorecard.py` | a project not checked yet |
| `tests/ui_v04.py` | --- brains in Settings |
| `tests/ui_v06.py` | --- existing folder |
| `tests/ui_v07.py` | --- Lite: no model controls; Claude picks the model for each step |
| `tests/ui_v08.py` | Browser test of 0.8.0 UI: prior-art and change-request cards, New Project language picker, |
| `tests/ui_v09.py` | Browser test of 0.9.0: three brain modes, test-before-add, per-model tier, usage in/out split + cache, |
| `tests/ui_v10.py` | Browser test of 0.10.0: in-app feedback button -> feature queue, requested-features page, |
| `tests/ui_v100.py` | L35: the version next to the app name |
| `tests/ui_v11.py` | Browser test of 0.11.0 brain fixes: |
| `tests/ui_v111.py` | Browser test of 0.11.1: broken project fixable from the list (66), reload in the ⋯ menu, plan usage rows (61/62), |
| `tests/ui_v112.py` | Browser test of 0.11.2: real plan figures from Claude (69), Launch for an existing folder with a start-file |
| `tests/ui_v11_usage.py` | Browser test of usage in Lite: refresh button + 2-minute auto refresh, one plain sentence. |
| `tests/ui_v130.py` | Browser test of 0.13.0: Archify diagrams in a Diagrams tab (Simple mode too), Open and What changed, Ask Fred box; |
| `tests/ui_v140.py` | Browser test of AI Team Lite 1.4.0 (L39): the window stays usable on half and on a quarter of the screen: no |
| `tests/ui_v162.py` | L60 |
| `tests/ui_voice_usage.py` | Browser test of talking (simulated microphone), the usage meter/tracker and the voice settings. |
| `tools/sandbox_probe.py` | ------------------------------------------------------------------ where the tools are (no side effects) |
| `WORKLIST-PRIVATE.md` | AI Team Lite: private work list (never published) |
