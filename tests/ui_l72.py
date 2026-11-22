"""Browser test of REQ-30 (L72): the improvement-ideas card, decisions shown, kept after reload, saved in reports/IDEAS.md.
Usage: python tests/ui_l72.py <screenshot folder>
Assumes demo_server.py (with DEMO_IDEAS=1) answers a message containing "ideas" by calling propose_ideas with 3 ideas."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws"), "pipeline_auto": False}))
os.environ.update(AITEAM_HOME=str(tmp / "d"), AITEAM_SECRETS_FILE=str(tmp / "s.json"), DEMO_IDEAS="1")
sys.path[:0] = [str(HERE.parent), str(HERE)]
from app import projects   # noqa: E402

p = projects.create("Ideas UI"); folder = projects.project_dir(p["slug"])
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
def open_project(page):
    page.locator("#plist li", has_text="Ideas UI").click(); page.wait_for_timeout(800)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url); page.wait_for_function("typeof S === 'object' && S && S.projects.length >= 1", timeout=8000)
        open_project(page)
        page.fill("#input", "show me improvement ideas"); page.click("#sendBtn")
        card = page.locator(".ideas-card"); expect(card).to_be_visible(timeout=15000)
        rows = card.locator("tr.idea-row")
        check("REQ-30 card shows 3 ideas", rows.count() == 3, rows.count())
        head = card.locator("thead").inner_text().lower()
        check("REQ-30 table has idea, why it helps, effort", all(w in head for w in ("idea", "why", "effort")), head)
        check("REQ-30 each row has a Yes and a No button",
              all(rows.nth(i).get_by_role("button", name="Yes").count() == 1 and rows.nth(i).get_by_role("button", name="No").count() == 1
                  for i in range(3)))
        check("REQ-30 optional reason box on each row", card.locator(".idea-reason").count() == 3)
        page.screenshot(path=OUT / "req30_ideas_card_open.png")
        rows.nth(0).locator(".idea-reason").fill("boss asked")
        rows.nth(2).locator(".idea-reason").fill("typed for row three")
        rows.nth(0).get_by_role("button", name="Yes").click()
        page.wait_for_timeout(800)
        check("REQ-30 reason typed in another row survives an answer",
              rows.nth(2).locator(".idea-reason").input_value() == "typed for row three",
              rows.nth(2).locator(".idea-reason").input_value())
        rows.nth(1).get_by_role("button", name="No").click()
        page.wait_for_timeout(800)
        check("REQ-30 Yes row shows its decision", "yes" in rows.nth(0).inner_text().lower())
        check("REQ-30 No row shows its decision", "no" in rows.nth(1).inner_text().lower())
        page.screenshot(path=OUT / "req30_two_rows_decided.png")
        page.goto(url); page.wait_for_function("typeof S === 'object' && S && S.projects.length >= 1", timeout=8000)
        open_project(page)
        rows = page.locator(".ideas-card tr.idea-row")
        check("REQ-30 decisions still shown after reload",
              rows.count() == 3 and "yes" in rows.nth(0).inner_text().lower() and "no" in rows.nth(1).inner_text().lower())
        page.screenshot(path=OUT / "req30_after_reload.png")
        md = folder / "reports" / "IDEAS.md"
        check("REQ-30 reports/IDEAS.md exists", md.exists())
        text = md.read_text(encoding="utf-8") if md.exists() else ""
        check("REQ-30 IDEAS.md holds both decisions and the reason",
              "boss asked" in text and "yes" in text.lower() and "no" in text.lower(), text[:200])
        b.close()
finally:
    proc.kill()
check("REQ-30 no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL L72 UI CHECKS PASSED")
