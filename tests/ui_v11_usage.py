"""Browser test of usage in Lite: refresh button + 2-minute auto refresh, one plain sentence."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "advanced_mode": True, "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json")}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.clock.install()
        page.goto(url)
        page.click("#newBtn"); page.fill("#newName", "Expense tracker"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        check("meter has a refresh button", page.locator("#meterRefresh").count() == 1)
        check("meter shows when it refreshed", "Refreshed" in page.locator("#usageMeter").inner_text())
        page.click("#usageMeter")
        expect(page.locator("#useDlg")).to_be_visible()
        check("dialog says when it refreshed", "refreshed" in page.locator("#useChecked").inner_text())
        check("plain sentence, no per-project tables", page.locator("#useBody table").count() == 0)
        page.click("#useRefresh")
        expect(page.locator(".err").last).to_contain_text("Usage refreshed", timeout=5000)
        page.clock.fast_forward(125000)
        page.wait_for_timeout(800)
        check("dialog still open and filled after the 2-minute refresh", page.locator("#useBody p").count() >= 1)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.11.0 USAGE UI CHECKS PASSED")
sys.exit(1 if fails else 0)
