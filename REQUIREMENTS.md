> Rebuilt by Fred from the existing files - please check.

# Requirements: AI Team Lite

## Problem
People who are not developers have business needs that a small tool could solve. They cannot write code, and
asking an AI chat for code gives them something they cannot check, run or hand to IT. AI Team Lite is a Windows
desktop app where Fred (an AI team lead) first checks the need is worth solving, then his AI team plans, builds,
tests and starts the tool in small steps. The owner (XaeriS) builds and uses it.

## Summary
AI Team Lite 1.6.2 is a Windows desktop app (Python server + pywebview window, plain HTML/JS screens). It drives
Claude through the Claude Agent SDK. It is the simple version of "AI Team", with its own settings, keys, sign-in and
projects.

## Users
- Non-technical people turning a business need into a prototype.
- The owner, who also works on AI Team Lite itself from inside AI Team Lite.

## Goals
- A prototype that is worth building, tested, and understandable by IT who take it over.
- Little risk to the user's PC, little usage of the Claude plan.

## Requirements

Existing (already built and released, verified only against the README, CHANGELOG, tests and code layout):

- **REQ-1 (existing) Setup.** When the user starts the app the first time, then Fred asks them to connect to Claude (subscription sign-in or API key) and walks through name, projects folder, GitHub and approvals. When no key or sign-in is present, then no project can be created. The API key is stored in Windows Credential Manager, never in a file and never shown to the AI.
- **REQ-2 (existing) Problem first.** When a new project starts, then Fred fills a Problem card (who, how often, cost today, how it is done now, success) and runs the challenge checklist before any requirement, with a "Why I ask" line per question, and saves reports/QUESTIONS.md.
- **REQ-3 (existing) Requirements and devil's advocate.** When requirements are written, then REQUIREMENTS.md holds "When ..., then ..." checks, and an independent challenger raises objections shown as cards (Keep current behaviour / Accept Fred's recommendation / Do something else); the user's reason is kept.
- **REQ-4 (existing) Prior-art check.** When the option is on, then the Scout searches the web and a card lists existing tools with links (reports/PRIOR_ART.md).
- **REQ-5 (existing) Architecture with Mat.** When requirements are agreed, then Mat asks about data, storage, security and hosting, and writes ARCHITECTURE.md; no plan is proposed before the user approves it.
- **REQ-6 (existing) Plan by iterations.** When the architecture is approved, then Fred proposes iterations with objections and answers; nothing is built until the user presses the approval button. Changes after approval go through a change-request card.
- **REQ-7 (existing) Build loop.** When an iteration runs, then tests are written first and must fail, the Developer builds one task at a time, the Tester records every test, the Reviewer checks, and the iteration is saved to Git only when the whole test suite passes.
- **REQ-8 (existing) Traceability and quality scorecard.** When a version is prepared, then each requirement is shown as proven or not proven by a test, and a plain-words scorecard (tests, code check, packages with osv.dev, leftover secrets, open findings, documents) is shown and saved as reports/QUALITY.md. Fixed versions of packages and security findings are tracked until fixed or accepted with a reason.
- **REQ-9 (existing) Approvals only for what is risky.** When a plan is approved (default mode), then the team works without asking inside the project folder, Windows temp and the app's folder; installing software, Windows changes, files elsewhere, programs from elsewhere, private files and GitHub still ask. Mode "Always" asks for everything. Text inside a file never triggers a false alarm.
- **REQ-10 (existing) Approve from the phone.** When Telegram is connected, then approval cards arrive with Approve/Decline buttons and stuck/limit alerts; only the owner's chat is accepted and high-risk actions stay at the PC unless allowed.
- **REQ-11 (existing) Work keeps moving.** When nothing moves for 3 minutes Fred says what runs; after 5 minutes the app wakes Fred, then restarts the step, then asks the user; never while a question waits. When the plan limit is reached, work resumes after reset. An expired sign-in shows one card with "Sign in again".
- **REQ-12 (existing) Pipeline and Team panel.** When several projects exist, then the Pipeline shows progress and projects can run in turn; the Team panel shows who works, waits or is idle.
- **REQ-13 (existing) Talking and typing.** When the user clicks the microphone, then speech becomes text on the PC; if it cannot hear, it says why and offers "Test microphone". Screenshots and files can be pasted or dropped; misspelt words get a red wavy line with corrections on right-click; chat text can be copied.
- **REQ-14 (existing) Usage and cost.** When the user clicks the usage meter, then they see cost per iteration, day, week, month, agent and phase, time per phase, and remaining Claude plan.
- **REQ-15 (existing) Checkpoints.** When an iteration is finished, then "What changed" shows requirement and files in plain words and "Go back to this point" restores it with an undo; nothing is deleted.
- **REQ-16 (existing) Existing folder.** When the user picks "Continue an existing folder", then Fred reads it read-only, rebuilds the missing documents and asks to confirm them. An oddly named folder never empties the project list.
- **REQ-17 (existing) Diagrams.** When the plan is approved, then a card asks whether to draw the overview diagram (Archify), shown in a Diagrams tab.
- **REQ-18 (existing) Council.** When a big decision comes up and brains (Gemini free tier, Ollama) are added, then the brains answer, review each other anonymously and Fred decides.
- **REQ-19 (existing) Delivery.** When a project is finished, then Fred can package a Windows installer (Start menu, icon, optional desktop shortcut, uninstaller, no admin rights), add GitHub tests and publish a release, each GitHub step with the user's approval. A whole-project clean-up runs after every code change, just before the push.
- **REQ-20 (existing) Layout and speed.** When the window is half or a quarter of the screen (down to 480 x 400), then everything stays usable; a long chat opens on its latest messages with "Show earlier messages".
- **REQ-21 (existing) Share and update.** The owner can publish AI Team Lite's source to a GitHub repo and the app checks for a newer version; the installer is built by GitHub's Release workflow on each push to main.

Still to do (from WORKLIST-PRIVATE.md), in the owner's order:

- **REQ-22 Sandbox for the team's commands (L47, planned 1.6.3, investigate first).** When the team runs a command, then a bad package or a tricked agent cannot reach the rest of the PC, with no setup effort for the user beyond a one-time permission. Step 1: a written comparison (restricted token + job object, AppContainer, Windows Sandbox, WSL, separate Windows user) with what each protects and which Windows editions it works on, and a recommendation. Step 2, only after the owner says "go": build it behind a setting and show a clear line on the approval card when a command runs outside the protection. When the Windows edition is Home, then Windows Sandbox is not offered.

- **REQ-28 Taskbar icon colour (CR-1, change request).** When any project waits for me (a question or an approval card), then the AI Team Lite icon on the taskbar (and next to the clock when the tray is on) is orange. When no project waits and at least one is working, then it is green. When nothing waits and nothing works, then it is grey. When both apply, orange wins. When I answer the last waiting card, then the icon leaves orange within a few seconds. When the window is hidden or minimised, then the colour still updates. When the colour cannot be shown, then the normal icon stays and nothing else breaks. The tooltip also says in words what the colour means.

Set aside by the owner on 2026-11-17 ("maybe later, not within the immediate need"):

- **REQ-23 (later) Parallel build** of independent tasks in separate Git worktrees (L50).
- **REQ-24 (later) Handover pack for IT** (L52a) and **signing the installer** (L52e).
- **REQ-25 (later) Portfolio view** across projects (L55).
- **REQ-26 (later) Reusable templates** (L56).
- **REQ-27 (later) A second person can review or approve** (L57).

## Out of scope
- Languages other than Python for the apps the team builds.
- Removed on purpose, do not bring back: Strix, Rust, Changes tab, Graphify, clean-up button, OmniRoute, ask_brain/delegate_code routing, model choice per role, Advanced mode.
- Changing the installed copy of AI Team Lite while it runs.

## Inputs and outputs
- In: the user's chat (typed or spoken), attached files and screenshots, existing project folders.
- Out: project documents (REQUIREMENTS, ARCHITECTURE, PLAN, STATUS), code, tests, reports/ (screenshots, test logs, QUALITY.md, QUESTIONS.md), Git commits, a Windows installer.

## Constraints
- Windows PC; bundled Python; app data in %APPDATA%\AI Team Lite; instance port 47824.
- Secrets only in Credential Manager. Nothing personal in public code.
- Every push to main publishes a public version: never push without the owner's approval; bump app/__init__.py, VERSION and CHANGELOG.md together.
- Commits use XaeriS <XaeriS@users.noreply.github.com>.
- Source files stay under about 500 lines. app/skills/* are other people's reviewed copies: do not edit.
- Tests: `python -m pytest -q` and every `tests/ui_*.py`; `python -m pyflakes app tests` prints nothing.

## Assumptions (to confirm)
- The existing features REQ-1 to REQ-21 work as the README and CHANGELOG say; I did not run them.
- The next version is 1.6.3 and starts with the L47 investigation.
- The owner's Windows edition is unknown (Home or Pro).

## Decisions
- 2026-11-17: L50, L55, L56, L57, L52a and L52e set aside by the owner.
- L47 is investigated first and built only after the owner's "go".

## Open questions
- Needs clarification: which of REQ-22 to REQ-27 matters most after the sandbox, and is anything missing?
- Needs clarification: what Windows edition does the owner's PC run?

## Checked against the code
- Version in VERSION is 1.6.2 and matches the latest CHANGELOG entry.
- The CHANGELOG dates 1.6.2 as 2026-11-21, while WORKLIST-PRIVATE.md is dated 2026-11-20 and the last commit is 1.6.2: the date in the CHANGELOG looks one day ahead.
- .gitignore has an uncommitted change adding `.aiteam/`; PROJECT_MAP.md is untracked.
