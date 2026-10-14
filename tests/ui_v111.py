"""Browser test of 0.11.1: broken project fixable from the list (66), reload in the ⋯ menu, plan usage rows (61/62),
Gemini add shows a counter and a clear result (63), recommendation reason (64), code graph viewer, tray option."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "advanced_mode": True, "workspace_dir": str(tmp / "ws")}))
(tmp / "d" / "external-projects.json").write_text(json.dumps({"gone-app": str(tmp / "no-longer-here")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_BRAIN": "1"}
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
        page.goto(url)
        # (66) a project that cannot be opened is listed and can be removed
        brk = page.locator("#plist li.broken")
        expect(brk).to_have_count(1, timeout=8000)
        check("broken project shown with a warning", "can't be opened" in brk.inner_text())
        brk.click()
        expect(page.locator("#brokenDlg")).to_be_visible()
        check("dialog explains why", "not found" in page.locator("#brkInfo").inner_text())
        check("offers to choose the new folder", page.locator("#brkPick").is_visible())
        page.screenshot(path=OUT / "h1-broken.png")
        page.click("#brkRemove")
        expect(page.locator("#plist li.broken")).to_have_count(0, timeout=8000)

        # (61/62) both plan windows always shown
        txt = page.locator("#usageMeter").inner_text()
        check("5-hour window shown", "Session (5 h)" in txt and "Week" in txt, txt)

        # code graph + reload in the menu
        page.click("#newBtn"); page.fill("#newName", "Expense tracker"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        slug = page.evaluate("cur")
        check("Diagrams tab offered", page.locator(".tab", has_text="Diagrams").count() == 1)
        page.click("#menuBtn")
        check("reload from its folder in the project menu", page.locator("#reloadBtn").is_visible())
        page.locator("#projDlg button[type=submit]").first.click()

        # tray option
        page.click("#settingsBtn")
        check("tray option present", page.locator("#setTray").count() == 1)
        page.check("#setTray")
        # (63/64) Gemini add: counter then clear result; Ollama recommendation reason
        page.click(".stab[data-s=brains]")
        page.select_option("#bProv", "gemini"); page.fill("#bKey", "AIza-demo"); page.click("#bLoad")
        expect(page.locator("#bLoadMsg")).to_contain_text("23 models", timeout=8000)
        page.click("#bAddBtn")
        expect(page.locator("#bMsg")).to_contain_text("added and working", timeout=40000)
        check("result names the model and says where it is", "appears in the list" in page.locator("#bMsg").inner_text())
        page.select_option("#bProv", "ollama")
        expect(page.locator("#bLoadMsg")).to_contain_text("strongest coding model", timeout=8000)
        check("best-for badges shown", page.locator("#bModelList .pill.acc").count() >= 2)
        page.screenshot(path=OUT / "h3-recommend.png", full_page=True)
        page.click("#setSave"); page.wait_for_timeout(500)
        page.click("#settingsBtn")
        check("tray option saved", page.locator("#setTray").is_checked())
        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.11.1 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
