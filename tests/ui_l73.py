"""Browser test of REQ-28 (L73): the "Flash the taskbar until I answer" setting in Settings > General.
Usage: python tests/ui_l73.py <screenshot folder>"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws"), "pipeline_auto": False}))
os.environ.update(AITEAM_HOME=str(tmp / "d"), AITEAM_SECRETS_FILE=str(tmp / "s.json"))

proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
BOX = "#setFlash"
def open_general(page):
    page.click("#settingsBtn"); page.click(".stab[data-s=gen]"); page.wait_for_timeout(300)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url); page.wait_for_function("typeof S === 'object' && S", timeout=8000)

        open_general(page)
        check("REQ-28 the flash checkbox is in Settings > General", page.locator(BOX).is_visible())
        label = page.locator("label", has=page.locator(BOX)).inner_text()
        check("REQ-28 it says 'Flash the taskbar until I answer'", "Flash the taskbar until I answer" in label, label)
        check("REQ-28 it is on by default", page.locator(BOX).is_checked())
        page.screenshot(path=OUT / "req28_flash_setting_default_on.png")

        page.uncheck(BOX); page.click("#setSave"); page.wait_for_timeout(600)
        open_general(page)
        check("REQ-28 switched off: it stays off when Settings is reopened", not page.locator(BOX).is_checked())

        page.goto(url); page.wait_for_function("typeof S === 'object' && S", timeout=8000)
        open_general(page)
        check("REQ-28 after reload it is still off", not page.locator(BOX).is_checked())
        page.screenshot(path=OUT / "req28_flash_setting_off_after_reload.png")
        b.close()
finally:
    proc.kill()
check("REQ-28 no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL L73 UI CHECKS PASSED")
