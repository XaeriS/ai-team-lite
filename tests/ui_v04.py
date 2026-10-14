"""Browser test of 0.4.0: brains settings, team graph, approval risk + brain explanation, tests view,
work queue (priority, pause/resume), limit card, clean-up."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
(tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
                                                     "subscription_signed_in": True, "user_name": "Fred", "advanced_mode": True,
                                                     "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_BRAIN": "1"}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond:
        fails.append(name)


def through_mat(page, expect):
    """0.6.0: after the requirements, Mat (lead architect) agrees the architecture before the plan."""
    expect(page.locator(".m.mat").last).to_contain_text("I'm Mat", timeout=10000)
    page.fill("#input", "Typed once, and yes encrypt it")
    page.keyboard.press("Enter")
    arch = page.locator(".card.pending", has_text="Architecture: Expense tracker")
    expect(arch).to_be_visible(timeout=10000)
    arch.locator("text=Approve architecture").click()


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 920})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(url)

        # --- brains in Settings
        page.click("#settingsBtn")
        page.click(".stab[data-s=brains]")
        expect(page.locator("#brainList")).to_contain_text("No council brains yet")
        page.select_option("#bProv", "ollama")
        check("Ollama needs no key", page.locator("#bKeyWrap").is_hidden())
        expect(page.locator("#bLoadMsg")).to_contain_text("2 already installed", timeout=8000)
        page.locator(".mitem[data-m='qwen2.5-coder:7b']").click()
        page.click("#bAddBtn")
        expect(page.locator("#bMsg")).to_contain_text("added", timeout=10000)
        check("brain tested then added", "✓" in page.locator("#bMsg").inner_text(), page.locator("#bMsg").inner_text())
        expect(page.locator(".brainrow")).to_have_count(1)
        page.select_option("#bProv", "gemini")
        check("Gemini shows the privacy warning", "improve Google" in page.locator("#bNote").inner_text())
        page.select_option("#bProv", "ollama")
        page.screenshot(path=OUT / "a1-brains.png")
        page.click("#setSave")

        # --- project + team graph
        page.click("#newBtn")
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").first).to_contain_text("What would you like to build", timeout=10000)
        check("Team tab is the default", page.locator(".tab.on").inner_text() == "Team")
        check("team graph has Fred, Mat, 3 workers and the brain", page.locator(".team-svg .tnode").count() == 6)
        page.fill("#input", "Track my expenses")
        page.keyboard.press("Enter")
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        page.fill("#input", "Desktop. Make a plan")
        page.keyboard.press("Enter")
        through_mat(page, expect)
        page.locator(".card.pending", has_text="Plan: Expense tracker v1").locator("text=Approve plan and start").click()

        # --- approval card: what / risk / brain explanation
        appr = page.locator(".card.pending", has_text="Run a command")
        expect(appr).to_be_visible(timeout=20000)
        check("risk badge is high", appr.locator(".risk.high").count() == 1)
        check("what-this-does line", "Installs software on this PC" in appr.inner_text(), appr.inner_text())
        page.locator(".tab", has_text="Team").click()
        check("Fred shown with a question (yellow)", "has a question" in page.locator(".tlist").inner_text())
        page.screenshot(path=OUT / "a2-approval-team.png")
        appr.locator("button", has_text="Decline").click()

        # --- tests view
        expect(page.locator(".m.fred").last).to_contain_text("starting the app", timeout=20000)
        page.locator(".tab", has_text="Tests").click()
        expect(page.locator(".trow-test")).to_have_count(3, timeout=5000)
        txt = page.locator("#pane").inner_text()
        check("tests summary 2 passed 1 failed", "2 passed" in txt and "1 failed" in txt, txt[:200])
        check("screenshot attached to its test", page.locator(".trow-test", has_text="monthly total per category").locator("img").count() == 1)
        check("open-folder button", page.locator("#openTests").count() == 1)
        page.screenshot(path=OUT / "a3-tests.png")
        check("iteration started card", page.locator(".itcard", has_text="Iteration 1 of 2 started").count() == 1)
        check("iteration committed card with commit id", page.locator(".itcard.done code").count() == 1
              and len(page.locator(".itcard.done code").inner_text()) >= 6)
        page.locator(".tab", has_text="Plan").click()
        expect(page.locator(".itrow")).to_have_count(2)
        check("plan tab: iteration 1 done, 2 planned", "done" in page.locator(".itrow").first.inner_text()
              and "planned" in page.locator(".itrow").nth(1).inner_text())
        check("header shows iteration progress", "iteration" in page.locator("#phase").inner_text().lower())
        page.screenshot(path=OUT / "a6-iterations.png")
        page.locator(".tab", has_text="Team").click()
        check("developer finished", "Finished: Build expense tracker v1" in page.locator(".tlist").inner_text())

        # --- work queue
        page.click("#workBtn")
        page.click(".wtab[data-w=run]")
        expect(page.locator("#workDlg .qrow[data-s]")).to_have_count(1)
        page.locator("#workDlg select[data-prio]").select_option("high")
        page.wait_for_timeout(300)
        page.locator("#workDlg button[data-act=pause]").click()
        expect(page.locator("#workDlg .qrow[data-s]")).to_contain_text("paused", timeout=5000)
        page.screenshot(path=OUT / "a4-work-queue.png")
        page.locator("#workDlg button[data-act=resume]").click()
        expect(page.locator("#workDlg .qrow[data-s]")).not_to_contain_text("paused", timeout=8000)
        check("priority saved", page.locator("#workDlg select[data-prio]").input_value() == "high")
        page.locator("#workDlg button[type=submit]").click()

        # --- limit card
        page.wait_for_timeout(1500)
        page.fill("#input", "please continue, simulate limit")
        page.keyboard.press("Enter")
        lim = page.locator(".card", has_text="Work stopped: plan usage limit reached")
        expect(lim).to_be_visible(timeout=15000)
        expect(lim.locator("[data-cd]")).to_contain_text("Continues automatically in")
        check("limit card has Continue now", lim.locator("button", has_text="Continue now").count() == 1)
        page.screenshot(path=OUT / "a5-limit.png")
        lim.locator("button", has_text="Cancel auto-continue").click()
        expect(page.locator(".card", has_text="Work stopped")).to_contain_text("Auto-continue cancelled", timeout=5000)

        check("no clean-up button in Lite", page.locator("#cleanBtn").count() == 0)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.4.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
