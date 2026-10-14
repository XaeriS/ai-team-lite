"""Browser test of 0.10.0: in-app feedback button -> feature queue, requested-features page,
Setup & connections tab (connections summary + share box), publish option folded away from General."""
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
        page.goto(url)

        # --- sidebar has the new buttons; no separate "Setup with Fred" button
        check("Send feedback button present", page.locator("#feedbackBtn").count() == 1)
        check("Requested features button present", page.locator("#featuresBtn").count() == 1)
        check("old separate Setup button removed", page.locator("#setupBtn").count() == 0)

        # --- send feedback (records locally; no GitHub configured)
        page.click("#feedbackBtn")
        expect(page.locator("#fbDlg")).to_be_visible()
        page.fill("#fbTitle", "Add a dark mode")
        page.fill("#fbBody", "Easier on the eyes at night")
        page.click("#fbSend")
        expect(page.locator("#fbMsg")).to_contain_text("Thank you", timeout=6000)
        page.screenshot(path=OUT / "f1-feedback.png")
        page.wait_for_timeout(1600)  # dialog auto-closes

        # --- requested features page shows it, owner can approve/reject
        page.click("#featuresBtn")
        expect(page.locator("#featDlg")).to_be_visible()
        check("feedback reached the queue", "Add a dark mode" in page.locator("#featBody").inner_text())
        # solo user (no repo) is treated as owner-capable: action buttons exist
        approve = page.locator(".feat", has_text="Add a dark mode").locator("button", has_text="Approve")
        check("approve button present", approve.count() >= 1)
        approve.first.click()
        expect(page.locator("#featBody")).to_contain_text("Approved", timeout=6000)
        page.screenshot(path=OUT / "f2-requested-features.png")
        page.locator("#featDlg button[type=submit]").click()

        # --- settings: General no longer has the publish button; Setup tab does the connections + sharing
        page.click("#settingsBtn")
        check("no publish button on General", page.locator("#publishApp").count() == 0)
        page.click(".stab[data-s=setup]")
        expect(page.locator("[data-sp=setup]")).to_be_visible()
        check("connections summary rendered", page.locator("#connList .env").count() >= 3)
        check("re-run setup button present", page.locator("#setupFredBtn").count() == 1)
        expect(page.locator("#shareBox")).to_contain_text("Create a new repository", timeout=6000)
        check("share setup button present", page.locator("#shareSetup").count() == 1)
        page.screenshot(path=OUT / "f3-setup-connections.png")
        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.10.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
