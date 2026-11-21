"""Browser test of REQ-29 (L71): each project keeps its own unsent message and attachments, saved with the project.
Usage: python tests/ui_l71.py <screenshot folder>"""
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

projects.create("Draft A"); projects.create("Draft B")
sample = tmp / "notes_a.csv"; sample.write_text("a,b\n1,2\n")

proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
box = lambda page: page.input_value("#input")
def open_project(page, name):
    page.locator("#plist li", has_text=name).click(); page.wait_for_timeout(600)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url); page.wait_for_function("typeof S === 'object' && S && S.projects.length >= 2", timeout=8000)

        open_project(page, "Draft A")
        page.fill("#input", "hello A")
        page.set_input_files("#fileIn", str(sample))
        expect(page.locator("#chips")).to_contain_text("notes_a", timeout=8000)
        page.screenshot(path=OUT / "req29_draft_in_project_a.png")

        open_project(page, "Draft B")
        check("REQ-29 switch to B: the box is empty", box(page) == "", box(page))
        check("REQ-29 switch to B: A's attachment is not shown", page.locator("#chips").inner_text().strip() == "")
        page.fill("#input", "hello B")
        page.screenshot(path=OUT / "req29_project_b_has_its_own_box.png")

        open_project(page, "Draft A")
        check("REQ-29 back in A: 'hello A' is there", box(page) == "hello A", box(page))
        check("REQ-29 back in A: the attachment is there", "notes_a" in page.locator("#chips").inner_text())
        page.screenshot(path=OUT / "req29_back_in_project_a.png")

        page.click("#sendBtn")
        expect(page.locator("#input")).to_have_value("", timeout=5000)
        check("REQ-29 send in A: A's attachment chip cleared", page.locator("#chips").inner_text().strip() == "")
        open_project(page, "Draft B")
        check("REQ-29 after send in A: B's draft is still 'hello B'", box(page) == "hello B", box(page))
        page.screenshot(path=OUT / "req29_send_in_a_keeps_b_draft.png")

        page.wait_for_timeout(2500)   # the draft is saved to the server about 1.5 s after typing stops
        page.goto(url); page.wait_for_function("typeof S === 'object' && S && S.projects.length >= 2", timeout=8000)
        open_project(page, "Draft B")
        check("REQ-29 after reload: B still shows 'hello B' (saved with the project)", box(page) == "hello B", box(page))
        open_project(page, "Draft A")
        check("REQ-29 after reload: A's box is empty (it was sent)", box(page) == "", box(page))
        page.screenshot(path=OUT / "req29_after_reload.png")
        b.close()
finally:
    proc.kill()
check("REQ-29 no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL L71 UI CHECKS PASSED")
