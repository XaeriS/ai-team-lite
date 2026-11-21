"""Timings and names shared by the engine's files (L46). Tests change some of them here."""
import re

AGENT_TOOLS = {"Agent", "Task"}
QUIET_TOOLS = {"ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"}  # engine housekeeping, not shown
ASYNC_RECEIPT = re.compile(r"Async agent launched", re.I)
AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
# These tools show their own card in the chat, so no extra activity line
CARD_TOOLS = {f"mcp__aiteam__{n}" for n in ("request_plan_approval", "ask_folder", "request_secret", "ask_choice",
                                            "take_screenshot", "show_image", "launch_for_user", "finish_setup",
                                            "start_iteration", "commit_iteration", "record_test_result",
                                            "handoff_to_architect", "request_architecture_approval", "record_problem",
                                            "record_challenge_review", "record_diagram",
                                            "return_to_fred", "record_prior_art", "ask_council",
                                            "record_change_request", "run_security_test", "record_questions", "connect_phone",
                                            "record_lesson")}
WORKER_LABELS = {"developer": "Developer", "tester": "Tester", "reviewer": "Reviewer", "scout": "Scout",
                 "challenger": "Challenger", "diagrammer": "Diagrammer"}
ROLES = ["Fred", "Mat", "Developer", "Tester", "Reviewer"]
LEADS = {"Fred": "lead", "Mat": "architect"}
STALL_SECONDS = 300
TERMINAL = {"completed", "failed", "stopped", "killed"}
WAKE_GRACE = 8          # after a background worker finishes, give the engine this long to wake Fred by itself (L7)
WATCH_POLL = 30         # how often the listener re-checks while a background worker runs
QUIET_NOTE = 180        # L8: nothing new for 3 minutes while working -> a short "still working" line
QUIET_STUCK = 300       # L8: nothing at all for 5 minutes (no tokens, no tool running) -> say so and offer help
COMMAND_STUCK = 1200    # a single command running for 20 minutes without any output is treated as stuck too
AUTO_HELP_MAX = 3       # L27: automatic wake-ups / restarts per project per hour; after that the user decides
WATCHDOG_TICK = 30
LIMIT_CHECK_SECONDS = 900   # L25: reset time unknown -> look at the plan's figures again every 15 minutes
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
PIPELINE_KICK_SECONDS = 1800   # never nudge the same iteration twice within this time
PIPELINE_TICK = 60
FRESH_MARKERS = ("start_iteration(", "Continue iteration", "user released iteration")  # safe context-reset points
CONTINUE_TEXT = ("[App] The Claude plan's usage limit has reset. Continue the work exactly where you stopped; do not "
                 "redo steps that were already finished. Tell the user in one line what you are resuming.")
MAX_MESSAGE_MB = 50   # L67: largest message accepted from Claude's engine (its default, 1 MB, stopped a project)
