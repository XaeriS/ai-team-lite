"""Browser test: delete attachments before/after sending; send and reply times on messages."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
                                                     "subscription_signed_in": True, "workspace_dir": str(tmp / "ws")}))
for n in ("bank-march.csv", "layout.xlsx"):
    (tmp / n).write_text("date,amount\n2026-03-01,12\n")
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json")}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1400, "height": 880})
        page.on("pageerror", lambda e: errs.append(str(e))); page.on("dialog", lambda d: d.accept())
        page.goto(url)
        page.click("#newBtn"); page.fill("#newName", "Budget"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").first).to_contain_text("What would you like", timeout=10000)
        check("Fred's greeting shows its time", page.locator(".m.fred").first.locator("xpath=..").locator(".meta").inner_text().strip() != "")
        # attach two files, remove one before sending
        page.set_input_files("#fileIn", [str(tmp / "bank-march.csv"), str(tmp / "layout.xlsx")])
        expect(page.locator("#chips .chip")).to_have_count(2)
        page.locator("#chips .chip", has_text="layout.xlsx").locator(".x").click()
        expect(page.locator("#chips .chip")).to_have_count(1)
        check("removed file deleted from the project", not (tmp / "ws" / "budget" / "inputs" / "layout.xlsx").exists())
        page.fill("#input", "Here is my bank export")
        page.keyboard.press("Enter")
        user = page.locator(".uwrap").last
        expect(user.locator(".chip")).to_have_count(1, timeout=5000)
        check("sent message shows the file as a chip, not raw text", "[Attached file" not in user.inner_text())
        expect(user.locator(".meta")).to_contain_text("Sent")
        expect(page.locator(".m.fred").last.locator("xpath=..").locator(".meta")).to_contain_text("replied after", timeout=10000)
        check("reply shows how long it took", "replied after" in page.locator(".m.fred").last.locator("xpath=..").locator(".meta").inner_text())
        page.screenshot(path=OUT / "f1-times-and-files.png")
        # delete after sending
        user.locator(".chip .x").click()
        expect(user.locator(".chip.gone")).to_have_count(1, timeout=5000)
        expect(page.locator(".notice", has_text="You deleted bank-march.csv")).to_be_visible()
        check("file gone from disk", not (tmp / "ws" / "budget" / "inputs" / "bank-march.csv").exists())
        page.reload()
        expect(page.locator(".uwrap .chip.gone")).to_have_count(1, timeout=10000)
        check("deleted state survives reload", True)
        page.screenshot(path=OUT / "f2-deleted.png")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL FILE/TIME UI CHECKS PASSED")
sys.exit(1 if fails else 0)
