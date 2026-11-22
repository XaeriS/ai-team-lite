# What changed

Each version is on the [Releases page](https://github.com/XaeriS/ai-team-lite/releases) with its installer.

## Unreleased
- **Taskbar colours across all projects.** A dot on the AI Team Lite taskbar icon (and tray icon) shows orange when a
  project waits for you, green while the team works, grey when nothing runs; the tooltip says it in words. The
  button flashes while something waits and you are elsewhere (Settings > General, "Flash the taskbar until I answer").
- **Improvement ideas after each iteration.** After an iteration is saved, Fred shows up to 3 ideas in a table
  (the idea, why it helps, effort). You answer Yes or No on each row, with an optional reason. Answers are kept
  after a restart and listed in reports/IDEAS.md; ideas you already decided are not suggested again.
- **One unsent message per project.** What you type in the message box (and the files or screenshots you attach)
  stays with its project: switch to another project and its own box appears; come back and your text is there
  again, even after closing the app. Sending clears only that project's box.

## 1.6.2 (2026-11-21)
- **Far fewer approval requests.** The team now works on your project without asking: anything inside the
  project folder, your Windows temp folder and AI Team Lite's own folder, and reading files. It still asks before
  anything risky for your PC: installing software or a new package, changing Windows settings, changing files
  elsewhere, running a program from elsewhere, reading private files (keys, passwords) and publishing to GitHub.
  No more false alarms from text inside a file (such as a test mentioning C:\Windows), recording a review
  finding never asks, and deleting inside the project is no longer shown as high risk.
- **Opening a project is fast again**, even with a very long chat: the latest messages show at once and
  "Show earlier messages" brings back the rest.
- **The right status after the last iteration:** "New version check" while the team is still checking the version,
  "Ready for your review" once it waits for you.
- **Fresh test results:** when an iteration passes and is saved, its results replace the older ones (one line per
  test); older logs and screenshots are removed (they stay in the project's saved checkpoints).
- **Spelling like Word:** a red wavy line under misspelt words; right-click one for corrections. The list under the
  message box is gone.
- **Big files no longer stop the work:** a very large message from Claude (a saved web page read in one go) used to
  end with an error; the limit is much higher and the work restarts by itself if it is reached.
- **Devil's advocate cards are clearer:** each point shows the current behaviour, the objection and Fred's
  recommendation, with three buttons: Keep current behaviour, Accept Fred's recommendation, Do something else.
- **Plan first, then the diagram:** after the architecture, Fred goes straight to the plan; once it is approved a
  card asks whether to draw the overview diagram now.
- **Clean-up last:** the whole-project clean-up now comes after every code change of a version (security fixes
  included), just before the push to GitHub; Fred no longer pushes halfway.
- **A real installer for the apps you build:** Fred can package a finished project as a Windows setup program
  (Start-menu entry, the app's icon, a "Create a desktop shortcut" box, an uninstaller, no administrator rights),
  add automatic tests on GitHub, and publish the installer as a GitHub release.

## 1.6.1 (2026-11-16)
- **Fixed: all projects disappeared** (with the setup guide, the Browse button and the token counts) after a folder
  whose name has capital letters or spaces (for example "VLC-Multiplayer") was added from inside the projects
  folder. That one folder stopped the whole list from loading. Such folders now work like any other project, and
  the app repairs the record by itself when it starts. Your files were never touched.
- One odd folder or damaged record can no longer empty the list: it is skipped and noted in the log.
- If the projects ever cannot be loaded, the window now says so and tries again, instead of staying empty.

## 1.6.0 (2026-11-16)
- **A quality check before each version, in plain words.** Before a new version is saved, Fred shows a short
  scorecard: is every requirement proven by a test, do the tests pass, does the code check find mistakes, do the
  packages the app uses have a fixed version and any known security problem, is a key or password written in the
  files, which review findings are still open (and which ones you accepted, with your reason), and are the
  documents up to date. Each line says OK, To look at or To fix; Fred fixes what must be fixed and checks again.
  The scorecard is in the chat, at the top of the Tests tab, and saved in the project as reports/QUALITY.md (handy
  for IT). When Fred asks to save the project to GitHub while points are still to fix, the approval card (and
  the phone message) says which ones; you still decide.
- The check for known security problems asks osv.dev (Google's free list) about the package names and versions
  only; switch it off in Settings > General if you prefer.

## 1.5.1 (2026-11-15)
- Housekeeping from the independent review: AI Team Lite's three biggest code files were split into smaller ones, each
  with one job. Nothing changes in how the app looks or behaves; it makes future fixes safer and faster.

## 1.5.0 (2026-11-14)
- **See what each iteration changed, in plain words.** Every finished iteration has a "What changed" button (in the
  Plan tab and on its card in the chat): the requirements it serves, then the files grouped as the app itself, tests,
  documents and settings, each marked new, changed or removed, with the changed lines on request.
- **Go back to an earlier iteration in one click.** "Go back to this point" puts the project's files back exactly as
  they were at the end of that iteration; the later iterations become "planned" again and Fred waits for you to say
  what to change. Nothing is deleted: work in progress is kept in a safety copy, everything stays in the project
  history, and "Undo" brings it all back.

## 1.4.0 (2026-11-13)
- **Works on half or a quarter of your screen.** The window can now be made much smaller (down to 480 x 400). In a
  narrow window the project list and menu slide in from the ☰ button, the side panel (Team, Plan, Tests...) slides in
  from its arrow, and the message box always stays in view. This also fixes the half-screen layout, where the side
  panel dropped under the project list.
- **Spelling help in the message box.** Possible typos are listed under what you type; click one (or right-click the
  word) to pick a correction or add the word to your dictionary. Choose the languages in Settings > General (English,
  French, Portuguese, Spanish, German, Italian, Dutch). Names, file names, links and code are left alone, and
  nothing leaves your PC.

## 1.3.7 (2026-11-12)
Improvements from an independent review of the app:
- **The project map lists your project, not library files.** Old Python environments under any name are skipped, so
  the team finds the right file faster and uses fewer tokens.
- **Documents follow the code.** An iteration is only saved once STATUS.md was updated (and a README exists as soon
  as the app can be started).
- **Review findings are tracked until closed.** Each finding stays in reports/FINDINGS.md until it is fixed, or you
  accept it with a reason; an accepted finding is not reported again.
- **Tidy-up before a version.** Fred lists leftovers (old Python environments, caches, backup copies) with their size
  and removes them only if you agree.
- Tests are named after the feature they prove, source files are kept small, and the usage window says when the
  model prices were last checked (with a warning when that is long ago).

## 1.3.6 (2026-11-11)
- **An expired Claude sign-in is explained, not repeated as red errors.** One card says "Your Claude sign-in has
  expired" with a **Sign in again** button; the team stops trying meanwhile, messages you send are kept for Fred, and
  the work continues by itself once you are signed in. A phone alert is sent if you use phone approvals.
- **When Windows closes Claude's engine** (a restart or an update), a plain note says so and the work continues on
  a fresh connection, instead of an error with a long number.

## 1.3.5 (2026-11-10)
- **Projects started in AI Team (or moved to another folder) work again.** Fred could answer every message with
  "No conversation found with session ID ...". He now notices that his earlier chat is not available, catches up
  from the project documents and carries on.

## 1.3.4 (2026-11-10)
- **The version is shown next to the app name** (top left), and in the window title, so you can see which version
  you have at a glance, also in the taskbar.

## 1.3.3 (2026-11-06)
- **No more "Access is denied" on project.json stopping the work.** Parts of the app that save a project's status at
  the same moment now take turns, a brief refusal by Windows (or an antivirus glancing at the file) is retried, and
  if a save still fails the team carries on instead of stopping. No change to a project is lost when two saves cross.

## 1.3.2 (2026-10-28)
- **No more windows flashing on your screen while the team works.** Claude's engine now runs with a hidden console,
  so the commands it runs (tests, Git, installs) no longer pop up black boxes, and test runs happen on a separate,
  invisible Windows desktop, so the prototype's own windows opened by tests stay out of your way. "Launch the
  project" and the screenshots Fred shows you still appear on your screen as before.
- No more red engine code (such as "[ede_diagnostic] ...") after you pause, stop or restart a project.

## 1.3.1 (2026-10-25)
- **Work restarts by itself after the plan limit, for real.** After a long wait, the work could stay stuck behind a
  step that never got going, and waking Fred up did not help. Continuing after the limit, "Restart this step" and
  waking Fred up now start cleanly on a fresh connection, in the same conversation. The limit is also registered at
  once, so no second "limit reached" card appears.

## 1.3.0 (2026-10-25)
- **Work keeps moving by itself.** After 5 minutes without any activity, AI Team Lite wakes Fred up and tells him
  which worker stopped; 5 minutes later it restarts the step. If that does not help, you choose. It never does this
  while someone waits for your answer.
- **A team graph you can trust.** Every worker (Diagrammer, Scout, Challenger) sits with the workers and has its own
  line to Fred; no more "no activity" on Fred while his worker is busy or waits for you.
- **Copy and paste in the chat.** Select any text, use the Copy button under a message, or right-click (Copy, and
  Paste in the message box).
- Code review and clean-up, with every test run again.

## 1.2.2 (2026-10-19)
- **No more hidden cards after a restart.** In long projects, an approval or question could fail to appear in the
  chat after the app had been restarted, so the work waited without you knowing. Every card now shows.

## 1.2.1 (2026-10-18)
- **Automatic restart after a plan limit works again.** When your Claude plan's 5-hour or weekly limit is reached,
  the work continues by itself once the limit resets. If the reset time isn't known, the app checks your plan every
  15 minutes and carries on as soon as there is room. The weekly limit is named correctly.

## 1.2.0 (2026-10-18)
- **Private screenshots:** a screenshot of a desktop app now shows only that app's window, never your other screens.
- **Faster and cheaper projects:** a project map so the team finds the right file first, short test results for
  the AI, only the related tests while building (the full suite still runs before saving), lessons learned carried
  across projects, and instructions arranged so Claude can reuse them.

## 1.1.1 (2026-10-17)
- Removed the orange "no tokens used for N minutes" warning, which fired during long but normal work such as test
  runs. The watchdog from 1.0.0 still tells you when something is really stuck.

## 1.1.0 (2026-10-15)
- **Approve from your phone:** with Telegram, approval cards reach your phone and you can answer them there, plus
  alerts when work is stuck or hits your plan's limit. Setup asks before installing anything.

## 1.0.1 (2026-10-14)
- Clean-up: unused code and repetition removed. No change in what the app does.

## 1.0.0 (2026-10-14)
- Never stuck silently: a watchdog checks quiet work and tells you what is happening, with a button to wake the team.
- Usage in plain words: cost per project and per finished iteration, by agent and by phase, time spent per phase,
  and how much of your Claude plan is left.
- "Why Fred asked this" under each challenge question, and a summary of the questions that changed your project.
- Paste or drop screenshots into the message box; a microphone test in Settings.

## 0.9.0 (2026-10-14)
- First version: the simple version of AI Team for people who don't write code.
