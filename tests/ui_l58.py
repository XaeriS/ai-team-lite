"""Browser test of L58: an odd folder name no longer empties the window; if loading ever fails, the window says so."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws"), "pipeline_auto": False}))
os.environ.update(AITEAM_HOME=str(tmp / "d"), AITEAM_SECRETS_FILE=str(tmp / "s.json"))
sys.path[:0] = [str(HERE.parent), str(HERE)]
from app import projects   # noqa: E402
from test_l58 import _odd_folder   # noqa: E402

projects.create("Invoices"); projects.create("Job applier")
_odd_folder()                                   # exactly as found on the owner's PC
projects.repair()                               # what the app does when it starts

proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        expect(page.locator("#plist li")).to_have_count(3, timeout=8000)
        names = page.locator("#plist li .nm").all_inner_texts()
        check("all projects listed, the odd folder included", sorted(names) == ["Invoices", "Job applier", "VLC multi-player"], names)
        page.locator("#plist li", has_text="VLC multi-player").click()
        expect(page.locator("#title")).to_contain_text("VLC multi-player", timeout=8000)
        page.click("#newBtn")
        page.locator("input[name=nkind][value=existing]").check()
        check("Browse button is on the New project screen (shown in the desktop app)", page.locator("#newBrowse").count() == 1)
        page.keyboard.press("Escape")
        page.screenshot(path=OUT / "v161-projects-back.png")
        # if the state ever fails, the window says so instead of staying empty
        page2 = b.new_page()
        page2.on("pageerror", lambda e: None)
        page2.route("**/api/state*", lambda r: r.fulfill(status=400, content_type="application/json", body='{"error": "Invalid project id"}'))
        page2.goto(url)
        expect(page2.locator("#plist")).to_contain_text("Projects not loaded", timeout=8000)
        check("failure is explained", "trying again" in page2.locator("#plist").inner_text())
        page2.screenshot(path=OUT / "v161-load-failed.png")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL L58 UI CHECKS PASSED")
