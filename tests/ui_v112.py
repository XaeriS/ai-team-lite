"""Browser test of 0.11.2: real plan figures from Claude (69), Launch for an existing folder with a start-file
choice (71)."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "advanced_mode": True, "workspace_dir": str(tmp / "ws")}))
# a stale 98% weekly figure from before the reset
(tmp / "d" / "usage.json").write_text(json.dumps({"seven_day": {"type": "seven_day", "label": "This week",
    "status": "allowed_warning", "percent": 98.0, "percent_at": 1, "seen": 1, "resets_at": None}}))
src = tmp / "accounting-formatting"; src.mkdir()
for f in ("run.bat", "run_app.bat", "run_migrate.bat", "REQUIREMENTS.md"):
    (src / f).write_text("x")
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_USAGE": "1"}
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
        expect(page.locator("#usageMeter")).to_contain_text("46%", timeout=10000)
        txt = page.locator("#usageMeter").inner_text()
        check("real session figure", "Session (5 h) 46%" in txt and "resets 7:30pm" in txt, txt)
        check("real week figure replaces the stale 98%", "Week 8%" in txt and "98" not in txt, txt)
        page.screenshot(path=OUT / "k1-usage.png")
        # existing folder with several start files
        page.click("#newBtn"); page.check("input[name=nkind][value=existing]")
        page.fill("#newFolder", str(src)); page.click("#newForm button[type=submit]")
        expect(page.locator("#launchBtn")).to_be_visible(timeout=10000)
        check("Launch shown for an existing folder", page.locator("#launchBtn").is_visible())
        page.click("#menuBtn")
        expect(page.locator("#startRow")).to_be_visible()
        page.click("#startBtn")
        expect(page.locator("#launchDlg")).to_be_visible()
        opts = page.locator("#launchList input").evaluate_all("els => els.map(e => e.value)")
        check("start files offered (not the migration script)", opts == ["run_app.bat", "run.bat"], opts)
        page.screenshot(path=OUT / "k2-choose-start.png")
        page.locator("#launchList input[value='run.bat']").check()
        page.click("#launchGo")
        expect(page.locator(".err").last).to_contain_text("starts with run.bat", timeout=6000)
        page.click("#menuBtn")
        check("choice remembered", "Now: run.bat" in page.locator("#startHint").inner_text())
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.11.2 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
