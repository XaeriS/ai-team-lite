# AI Team Lite by Fred

[![Tests](https://github.com/XaeriS/ai-team-lite/actions/workflows/tests.yml/badge.svg)](https://github.com/XaeriS/ai-team-lite/actions/workflows/tests.yml)

**[⬇ Download AI Team Lite for Windows](https://github.com/XaeriS/ai-team-lite/releases/latest)** (the installer is
under "Assets" on that page). Windows may say it does not recognise the app, because the installer is not signed:
click "More info", then "Run anyway". What changed in each version: [CHANGELOG.md](CHANGELOG.md).

> **Prototype.** AI Team Lite is a personal project, not an audited product. Its AI agents write and run code on
> your computer, and the web is not a safe place: downloaded code, packages and websites can be malicious, and an
> AI can be tricked. Try it on a test machine or in a separate folder, keep real business data and passwords away
> from it, read every approval before you click yes, and have someone qualified review anything it builds before
> you rely on it.

Turn a business need into a working prototype without writing code. You talk (or type) with Fred, your AI team
lead. Before anything is built he makes sure the problem is worth solving; then his AI team plans, builds and
tests it in small steps, and starts it for you to try. All you need is a Windows PC and a Claude subscription
(or an Anthropic API key).

## What makes it different

- **Problem first.** Fred agrees with you who is affected, how often, what it costs today, how it is done now and
  what success looks like, before writing a single requirement.
- **Challenged like a business analyst would:** does a tool you already have do it, what happens upstream and
  downstream, does it create a second copy of your data, who maintains it. Fred may advise a simpler way.
- **A devil's advocate** who has not seen your conversation reads the requirements cold and raises objections,
  contradictions, assumptions and vague points. You decide on each one; you can always overrule, and your reason
  is kept so the people who take it over later understand why.
- **Ready for IT to take further:** requirements with plain "When ..., then ..." checks, each one proven by a test,
  the architecture, the decisions and their reasons, and diagrams.
- **You learn the questions, not just the answers:** each challenge shows "Why Fred asked this", and the problem
  stage ends with "The questions that changed your project" (saved as reports/QUESTIONS.md to reuse).

## First start

- Connect Fred to Claude: sign in with your Claude subscription (Anthropic's own sign-in page) or paste an
  Anthropic API key. A key is stored in Windows Credential Manager, never in a file and never shown to the AI.
- Fred then walks you through setup: your name, your projects folder, GitHub (optional) and approvals.

## How a project is built

1. Fred and you agree the problem, then the requirements. The devil's advocate reviews them; you decide.
2. Optionally the Scout checks the web for existing tools you could reuse instead of building.
3. Mat, the lead architect, agrees how it will work (data, storage, security, where it runs) and asks you to
   approve the architecture.
4. Fred proposes a plan split into iterations. Nothing is built until you approve it.
5. Each iteration: tests written first, then the code, then a review; it is saved (Git) only when the tests pass.
6. When it is ready, "Launch the project" starts it for you to try. Diagrams show how it works.

Want to carry on with something that already exists? **+ New project > Continue an existing folder**: Fred reads it
(read-only) and carries on as if the team had built it.

## Approvals and safety

- Fred explains what he wants to do and the app asks you to Approve or Decline.
- **Only for big steps** (default): once you approve a plan, the team works inside that project's folder.
  Installing software, GitHub and anything outside the project folder always ask.
- **Always**: every action asks. Change it in Settings > General.
- Before each new version, the Reviewer runs a security review of the code.
- **Approve from your phone** (optional): when the team waits for you while you are away, a Telegram message arrives
  with Approve / Decline buttons, plus alerts when work is stuck or hits your plan's limit. Fred asks your
  permission and sets it up with you (Settings > Setup & connections). Nothing is installed on the PC and no door
  is opened on it; only your own chat is accepted; high-risk actions stay at the PC unless you allow them.

## Work keeps moving

- **Never stuck silently.** If nothing moves for 3 minutes, Fred says what is still running. After 5 minutes
  without any activity, AI Team Lite wakes Fred up by itself and tells him which worker stopped; if nothing moves
  5 minutes later, it restarts the step. If that does not help, it leaves the choice to you ("Wake Fred up" or
  "Stop and restart this step"). It never does this while someone waits for your answer.
- **Plan limits handled for you.** When your Claude plan's 5-hour or weekly limit is reached, the work continues by
  itself once the limit resets.
- **The Team panel** shows who is working, who has a question for you and who is idle, live.
- **The Pipeline** shows every project's progress; you can let several projects work in turn.

## Talking with Fred

- Type, or click the microphone and speak; say "copy" or pause to send. If it cannot hear you, it says why (no
  microphone, Windows privacy settings, microphone busy) and offers a "Test microphone" button (also in Settings >
  General).
- Paste a screenshot (Win+Shift+S, then Ctrl+V) or drop files straight into the message box.
- Copy anything from the chat: select text, or use the **Copy** button under a message, or right-click (Copy, and
  Paste in the message box).

## Usage and cost, in plain words

Click the usage meter: what the prototype cost (or would cost at API prices) per iteration, per day / week / month /
year, by agent and by phase; the time each phase took (waiting for you counted apart); live activity per minute and
per hour; and how much of your Claude plan is left.

## Everything else is set up for you

- Claude chooses the right model for each step (the most capable one only where it is needed).
- Built to save time and tokens: a project map (PROJECT_MAP.md) so the team opens the right file first, short test
  results for the AI (full logs kept in reports/test-logs), only the related tests while building (the full suite
  before every save), and lessons learned on earlier projects (Settings > General) passed on to new ones.
- Python with everything included; Git, the diagram tool and the speech model are bundled or installed for you.
- **Big decisions: the council.** Fred can ask extra AI brains for their own answer; they review each other
  anonymously and Fred decides as chairman. Add free brains in Settings > Council: Google Gemini's free tier
  (note: Google may use what is sent on its free tier) or Ollama, which runs on your PC.
- Share with friends and receive updates: Settings > Setup & connections.

## AI Team Lite and AI Team

AI Team Lite is the simple version of AI Team, for people who are not developers. It keeps its own settings, keys,
sign-in and projects, so both can be installed on the same PC.

## Licence

[MIT](LICENSE).
