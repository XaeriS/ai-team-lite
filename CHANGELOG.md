# What changed

Each version is on the [Releases page](https://github.com/XaeriS/ai-team-lite/releases) with its installer.

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
