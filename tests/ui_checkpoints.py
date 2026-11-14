"""Browser test of L48: the "What changed" window of an iteration (plain words, requirement, files by kind, the lines
on request) and going back to an earlier iteration, then undoing it."""
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
from test_checkpoints import _project   # a real Git project with three finished iterations
slug, folder = _project("Invoices")
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
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
        page.wait_for_function("typeof S === 'object' && S", timeout=8000)
        page.locator("#plist li", has_text="Invoices").click()
        page.click(".tab[data-t=plan]")
        expect(page.locator("#pane [data-changes]")).to_have_count(3, timeout=8000)
        page.locator("#pane [data-changes='2']").click()
        body = page.locator("#chgBody")
        expect(body).to_contain_text("The app itself", timeout=8000)
        text = body.inner_text()
        check("title names the iteration", "What iteration 2 changed: Totals" in page.locator("#chgTitle").inner_text())
        check("says which requirement it serves", "REQ-2" in text and "Monthly totals" in text, text[:300])
        check("files grouped in plain words", all(x in text for x in ("The app itself", "Tests (the proof that it works)", "Documents", "Settings and data files")))
        check("each file says what happened", "app/totals.py" in text and "new" in text and "removed" in text and "1 line added, 1 line removed" in text, text[:600])
        row = body.locator(".chgfile[data-path='app/importer.py']")
        row.locator("[data-lines]").click()
        expect(row.locator(".chglines")).to_contain_text("return [1, 2]", timeout=5000)
        check("the changed lines can be shown", row.locator(".chglines .ladd").count() >= 1 and row.locator(".chglines .ldel").count() >= 1)
        check("go back is offered with what it undoes", "Go back to this point" in text and "iteration 3 (Export)" in text and "Nothing is deleted" in text)
        page.screenshot(path=OUT / "v141-what-changed.png")
        page.click("#chgBack")
        check("going back asks to confirm", "Yes, go back to iteration 2" in page.locator("#chgBack").inner_text() and (folder / "app" / "export.py").exists())
        page.click("#chgBack")
        card = page.locator(".card", has_text="You went back to the end of iteration 2")
        expect(card).to_have_count(1, timeout=10000)
        check("files are back as they were", not (folder / "app" / "export.py").exists() and (folder / "app" / "totals.py").exists())
        check("the card says what happened", "Planned again: iteration 3 (Export)" in card.inner_text() and "Nothing was deleted" in card.inner_text())
        page.click(".tab[data-t=plan]")
        expect(page.locator("#pane [data-changes]")).to_have_count(2, timeout=8000)
        check("iteration 3 is planned again", "planned" in page.locator("#pane .itrow").nth(2).inner_text())
        page.screenshot(path=OUT / "v141-went-back.png")
        card.locator("[data-a=undo]").click()
        expect(page.locator(".card", has_text="You went back to the end of iteration 2").locator(".pill", has_text="undone")).to_have_count(1, timeout=10000)
        check("undo brings everything back", (folder / "app" / "export.py").exists())
        page.click(".tab[data-t=plan]")
        expect(page.locator("#pane [data-changes]")).to_have_count(3, timeout=8000)
        # the last iteration has nothing after it: no go-back offered
        page.locator("#pane [data-changes='3']").click()
        expect(page.locator("#chgBody")).to_contain_text("The app itself", timeout=8000)
        check("no go-back when nothing came after", page.locator("#chgBack").count() == 0)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL CHECKPOINT UI CHECKS PASSED")
