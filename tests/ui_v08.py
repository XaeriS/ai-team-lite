"""Browser test of 0.8.0 UI: prior-art and change-request cards, New Project language picker,
Settings "Team & tools" tab (skills, security, environment), and the interrupted-work banner."""
import json
import os
import subprocess
import sys
import tempfile
import time
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

        # --- New Project: no language picker (Lite builds in Python)
        page.click("#newBtn")
        check("no language picker", page.locator("#newLang, #newLangWrap").count() == 0)
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)

        # --- prior-art + change-request cards
        page.fill("#input", "show v08 cards")
        page.keyboard.press("Enter")
        pa = page.locator(".card.pa", has_text="Existing solutions found")
        expect(pa).to_be_visible(timeout=10000)
        check("prior-art lists a reusable tool with a link", pa.locator("a", has_text="sqlite-utils").count() == 1)
        check("prior-art shows closeness tags", pa.locator(".close").count() >= 2)
        check("prior-art recommendation", "Reuse sqlite-utils" in pa.inner_text())
        page.screenshot(path=OUT / "d2-prior-art.png")
        cr = page.locator(".card.pending", has_text="Change request")
        expect(cr).to_be_visible(timeout=10000)
        check("change request shows impact", "REQ-1" in cr.inner_text() and "dark" in cr.inner_text().lower())
        page.screenshot(path=OUT / "d3-change-request.png")
        cr.locator("button", has_text="Approve change").click()
        expect(page.locator(".card", has_text="Change request")).to_contain_text("approved", timeout=8000)

        # --- Settings: no Team & tools tab (the team runs with its defaults)
        page.click("#settingsBtn")
        check("no Team & tools tab", page.locator(".stab[data-s=team]").count() == 0)
        check("three tabs: General, Council, Setup", page.locator(".stab").count() == 3)
        page.click("#setCancel")

        # --- interrupted-work banner (seed an unclean shutdown for the project)
        (tmp / "d" / "interrupted.json").write_text(json.dumps({"items": [
            {"slug": "expense-tracker", "name": "Expense tracker", "active": True, "time": time.time() - 300,
             "iteration": 1, "iteration_name": "Add expenses", "task": "storage"}]}))
        page.reload()
        banner = page.locator(".banner", has_text="Work was interrupted")
        expect(banner).to_be_visible(timeout=10000)
        check("banner names the project and iteration", "Expense tracker" in banner.inner_text()
              and "iteration 1" in banner.inner_text())
        check("banner offers Resume and Restart", banner.locator("button", has_text="Resume").count() == 1
              and banner.locator("button", has_text="Restart the step").count() == 1)
        page.screenshot(path=OUT / "d5-interrupted.png")
        banner.locator("button", has_text="Resume").first.click()
        expect(page.locator(".banner")).to_have_count(0, timeout=8000)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.8.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
