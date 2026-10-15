"""Browser test of 0.7.0: brains (Claude included) + cancel, usage per brain/project with costs, side panel
resize/collapse, per-iteration release gates, pipeline screen, launch button."""
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
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json")}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond:
        fails.append(name)



try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 920})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(url)

        # --- Lite: no model controls; Claude picks the model for each step
        page.click("#settingsBtn")
        page.click(".stab[data-s=brains]")
        check("no Claude model list or price table", page.locator("#claudeList, #roleBox, #priceTable, #mAuto").count() == 0)
        check("council explained", "council" in page.locator("[data-sp=brains]").inner_text())
        page.click("#setCancel")
        check("cancel closes", not page.locator("#setDlg").is_visible())

        # --- project to the plan
        page.click("#newBtn")
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        side_w = page.locator("#side").bounding_box()["width"]
        check("side panel is wider (about 40%)", side_w >= 500, str(side_w))
        page.fill("#input", "Track my expenses")
        page.keyboard.press("Enter")
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        page.fill("#input", "Desktop. Make a plan")
        page.keyboard.press("Enter")
        expect(page.locator(".m.mat").last).to_contain_text("I'm Mat", timeout=10000)
        page.fill("#input", "Typed once, encrypt it")
        page.keyboard.press("Enter")
        page.locator(".card.pending", has_text="Architecture").locator("text=Approve architecture").click()
        plan = page.locator(".card.pending", has_text="Plan: Expense tracker v1")
        expect(plan).to_be_visible(timeout=10000)
        check("per-iteration choice on the plan card", plan.locator("select[data-after]").count() == 1)
        check("no dollar estimate on the plan card", "Estimated cost" not in plan.inner_text())
        plan.locator("select[data-after='1']").select_option("pause")
        page.screenshot(path=OUT / "c2-plan-gates.png")
        plan.locator("text=Approve plan and start").click()
        appr = page.locator(".card.pending", has_text="Run a command")
        expect(appr).to_be_visible(timeout=20000)
        appr.locator("button", has_text="Decline").click()
        rel = page.locator(".itcard", has_text="waiting for your release")
        expect(rel).to_be_visible(timeout=20000)
        expect(page.locator("#stateP")).to_have_text("Waiting for release", timeout=10000)
        check("sidebar shows waiting for release", "Waiting for release" in page.locator("#plist li.sel").inner_text())

        # --- launch button (run.bat cannot run on this test machine: the error path is shown)
        expect(page.locator("#launchBtn")).to_be_visible(timeout=5000)
        page.click("#launchBtn")
        err = page.locator(".err", has_text="Could not start it")
        expect(err).to_be_visible(timeout=10000)
        check("offers to ask Fred to fix the launch", err.locator("button", has_text="Ask Fred to fix it").count() == 1)
        page.screenshot(path=OUT / "c3-launch-error.png")
        err.locator("button", has_text="Close").click()

        # --- layout: collapse and bring back the side panel
        page.click("#sideToggle")
        expect(page.locator("#side")).to_be_hidden()
        check("chat uses the full width", page.locator("main").bounding_box()["width"] > 1100)
        page.screenshot(path=OUT / "c4-panel-hidden.png")
        page.click("#sideToggle")
        expect(page.locator("#side")).to_be_visible()

        # --- pipeline screen: release
        page.click("#workBtn")
        row = page.locator(".pipe-proj", has_text="Expense tracker")
        expect(row).to_be_visible(timeout=5000)
        check("iteration 2 waiting for release", "waiting for release" in row.inner_text())
        page.screenshot(path=OUT / "c5-pipeline.png")
        row.locator("button[data-release]").click()
        expect(row).not_to_contain_text("waiting for release", timeout=10000)
        page.locator("#workDlg button[type=submit]").click()
        expect(page.locator(".m.fred").last).to_contain_text("Starting iteration 2", timeout=10000)
        check("iteration 2 started", page.locator(".itcard", has_text="Iteration 2 of 2 started").count() == 1)

        # --- usage: one plain sentence, no token tables or dollar amounts
        page.click("#usageMeter")
        expect(page.locator("#useDlg")).to_be_visible()
        txt = page.locator("#useBody").inner_text()
        check("plain usage words", "Not billed" in txt or "Weekly limit" in txt, txt[:200])
        check("no per-model tables (1.0.0 shows simple per-iteration and per-period tables)", "By brain" not in txt
              and "Claude Sonnet" not in txt and page.locator("#useBody tr[data-it]").count() >= 1)
        page.locator("#useDlg button[type=submit]").click()
        check("header shows no dollar amount", "$" not in page.locator("#cost").inner_text(), page.locator("#cost").inner_text())
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.7.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
