"""Browser test of 0.6.0: project from an existing folder, Mat the lead architect, hide/delete, idle state."""
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
old = tmp / "old" / "price-watcher"
(old / "src").mkdir(parents=True)
(old / "src" / "main.py").write_text("print('prices')\n")
(old / "README.md").write_text("# Price watcher\nTODO: daily refresh\n")
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

        # --- existing folder
        page.click("#newBtn")
        check("existing-folder fields hidden at first", page.locator("#newExisting").is_hidden())
        page.locator("input[name=nkind][value=existing]").check()
        expect(page.locator("#newExisting")).to_be_visible()
        page.fill("#newFolder", str(old))
        page.fill("#newName", "Price watcher")
        page.screenshot(path=OUT / "b1-new-existing.png")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("Which matters most", timeout=15000)
        check("scan summary: where we are", "Where we are" in page.locator("#msgs").inner_text())
        check("read-only git log ran without asking", page.locator(".card", has_text="Run a command").count() == 0)
        check("Status tab exists", page.locator(".tab", has_text="Status").count() == 1)
        page.locator(".tab", has_text="Status").click()
        expect(page.locator("#pane")).to_contain_text("Daily automatic refresh", timeout=5000)
        expect(page.locator("#stateP")).to_have_text("Idle", timeout=5000)
        check("sidebar shows Idle", "Idle" in page.locator("#plist li.sel").inner_text())
        page.screenshot(path=OUT / "b2-existing-status.png")
        check("original files untouched", (old / "src" / "main.py").read_text() == "print('prices')\n")

        # --- new project through Mat
        page.click("#newBtn")
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        page.fill("#input", "Track my expenses")
        page.keyboard.press("Enter")
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        page.fill("#input", "Desktop. Make a plan")
        page.keyboard.press("Enter")
        expect(page.locator(".handoff")).to_contain_text("Fred handed over to Mat", timeout=10000)
        expect(page.locator(".m.mat").last).to_contain_text("I'm Mat", timeout=10000)
        check("Mat's messages are labelled", "Mat · lead architect" in page.locator(".who.mat").last.inner_text())
        expect(page.locator("#phase")).to_contain_text("Architecture", timeout=5000)
        check("composer talks to Mat", "Message Mat" in page.get_attribute("#input", "placeholder"))
        page.locator(".tab", has_text="Team").click()
        check("Mat in the team graph", page.locator(".team-svg .tnode[data-n=Mat]").count() == 1)
        page.screenshot(path=OUT / "b3-mat-questions.png")
        page.fill("#input", "Typed once, and yes encrypt it")
        page.keyboard.press("Enter")
        arch = page.locator(".card.pending", has_text="Architecture: Expense tracker")
        expect(arch).to_be_visible(timeout=10000)
        check("decisions table", arch.locator("table.dec tr").count() == 5)
        check("encryption decision shown", "Windows Credential Manager" in arch.inner_text())
        page.locator(".tab", has_text="Architecture").click()
        expect(page.locator("#pane")).to_contain_text("not approved yet", timeout=5000)
        page.screenshot(path=OUT / "b4-architecture-card.png")
        arch.locator("text=Request changes").click()
        arch.locator("textarea").fill("Back up every day instead")
        arch.locator("text=Send to Mat").click()
        expect(page.locator(".m.mat").last).to_contain_text("adjust the architecture", timeout=10000)
        page.fill("#input", "Daily backups, then it is fine")
        page.keyboard.press("Enter")
        arch2 = page.locator(".card.pending", has_text="Architecture: Expense tracker")
        expect(arch2).to_be_visible(timeout=10000)
        check("first architecture card shows changes requested",
              page.locator(".card.arch", has_text="changes requested").count() == 1)
        arch2.locator("text=Approve architecture").click()
        plan = page.locator(".card.pending", has_text="Plan: Expense tracker v1")
        expect(plan).to_be_visible(timeout=10000)
        check("handed back to Fred", page.locator(".handoff", has_text="Mat handed back to Fred").count() == 1)
        expect(page.locator("#pane")).to_contain_text("approved", timeout=5000)
        page.screenshot(path=OUT / "b5-back-to-fred-plan.png")
        plan.locator("text=Request changes").click()
        plan.locator("textarea").fill("later")
        plan.locator("text=Send to Fred").click()
        expect(page.locator("#stateP")).to_have_text("Idle", timeout=10000)

        # --- hide / show / delete
        page.click("#menuBtn")
        expect(page.locator("#projDlg")).to_be_visible()
        check("no keep-files option for a project AI Team Lite created", page.locator("#removeWrap").is_hidden())
        check("delete disabled until the name is typed", page.locator("#delBtn").is_disabled())
        page.screenshot(path=OUT / "b6-project-menu.png")
        page.click("#hideBtn")
        expect(page.locator("#plist")).not_to_contain_text("Expense tracker", timeout=5000)
        expect(page.locator("#hiddenBtn")).to_contain_text("Show hidden projects (1)")
        page.click("#hiddenBtn")
        expect(page.locator("#plist")).to_contain_text("Expense tracker (hidden)")
        page.locator("#plist li", has_text="Expense tracker").click()
        page.click("#menuBtn")
        page.click("#hideBtn")
        expect(page.locator("#plist")).not_to_contain_text("(hidden)", timeout=5000)
        page.click("#menuBtn")
        page.fill("#delConfirm", "Expense tracke")
        check("still disabled with a wrong name", page.locator("#delBtn").is_disabled())
        page.fill("#delConfirm", "Expense tracker")
        page.click("#delBtn")
        expect(page.locator("#plist")).not_to_contain_text("Expense tracker", timeout=5000)
        check("project folder deleted", not (tmp / "ws" / "expense-tracker").exists())
        # existing folder: remove but keep files
        page.locator("#plist li", has_text="Price watcher").click()
        page.click("#menuBtn")
        check("keep-files option for an existing folder", page.locator("#removeWrap").is_visible())
        page.click("#removeBtn")
        expect(page.locator("#plist")).not_to_contain_text("Price watcher", timeout=5000)
        check("files kept, history kept in the folder for reopening", (old / "src" / "main.py").exists() and (old / ".aiteam").exists()
              and (old / "STATUS.md").exists())
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.6.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
