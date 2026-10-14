"""Browser test of 0.14.0 (item 79): the Problem card, the devil's advocate card (decide each point, overrule with a
reason, answers required for vague points), proof by tests in the Requirements tab, challenge level in Settings."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws"), "prior_art": False}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json")}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)

def say(page, text):
    page.fill("#input", text); page.keyboard.press("Enter")

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        page.click("#newBtn"); page.fill("#newName", "Expenses"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        page.locator("#tabs button", has_text="Requirements").click()
        expect(page.locator("#pane")).to_contain_text("what problem you are solving", timeout=6000)
        check("empty Requirements tab says problem comes first", True)
        say(page, "I want to track my expenses")
        card = page.locator(".card", has_text="The problem we are solving").first
        expect(card).to_be_visible(timeout=10000)
        check("problem card in the chat", "Every week" in card.inner_text() and "Complete" in card.inner_text())
        expect(page.locator("#pane")).to_contain_text("The problem we are solving", timeout=6000)
        check("problem card on top of the Requirements tab", "monthly totals cannot be trusted" in page.locator("#pane").inner_text())
        page.screenshot(path=OUT / "p1-problem.png")
        # the review only runs when the demo is asked to challenge
        say(page, "make a plan and challenge me")
        ch = page.locator(".card.pending", has_text="Devil's advocate")
        expect(ch).to_be_visible(timeout=10000)
        check("four points, one of each kind", ch.locator(".chf").count() == 4)
        check("send disabled until all decided", ch.locator("[data-send]").is_disabled())
        boxes = ch.locator(".chf")
        boxes.nth(0).locator("[data-d=keep]").click()
        boxes.nth(0).locator("[data-why]").fill("My manager asked for an app")
        boxes.nth(1).locator("[data-d=change]").click()
        boxes.nth(2).locator("[data-d=confirm]").click()
        check("confirm needs no reason", boxes.nth(2).locator("[data-why]").is_hidden())
        boxes.nth(3).locator("[data-d=answer]").click()
        check("an answer is required for a vague point", ch.locator("[data-send]").is_disabled()
              and "1 point(s) left" in ch.inner_text())
        boxes.nth(3).locator("[data-why]").fill("Treat it as a refund")
        check("all decided enables send", ch.locator("[data-send]").is_enabled())
        page.screenshot(path=OUT / "p2-devils-advocate.png")
        ch.locator("[data-send]").click()
        done = page.locator(".card", has_text="Devil's advocate").first
        expect(done).to_contain_text("Your decision: keep as is", timeout=8000)
        check("decisions shown on the answered card", "My manager asked for an app" in done.inner_text())
        expect(page.locator(".handoff").last).to_contain_text("Fred handed over to Mat", timeout=10000)
        check("handoff to Mat after the review", True)
        page.locator("#tabs button", has_text="Plan").click(); page.locator("#tabs button", has_text="Requirements").click()
        expect(page.locator("#pane")).to_contain_text("Proof by tests", timeout=6000)
        check("proof table lists requirements", page.locator("#proofTable tr").count() == 2
              and "not tested yet" in page.locator("#proofTable").inner_text())
        check("review summary with overruled count", "1 overruled by you" in page.locator("#pane").inner_text())
        # a passing test linked to REQ-1 proves it
        os.environ.update(AITEAM_HOME=str(tmp / "d"), AITEAM_SECRETS_FILE=str(tmp / "s.json"))
        sys.path.insert(0, str(HERE.parent))
        from app import projects, tools
        tools.add_test_result(projects.project_dir(page.evaluate("cur")), {"name": "add expense", "requirement": "REQ-1", "status": "passed"})
        page.locator("#tabs button", has_text="Plan").click(); page.locator("#tabs button", has_text="Requirements").click()
        expect(page.locator("#proofTable")).to_contain_text("proven by test", timeout=6000)
        check("1 of 2 proven", "1 of 2 proven" in page.locator("#pane").inner_text())
        page.screenshot(path=OUT / "p3-proof.png")
        # Settings: challenge level only in Advanced mode
        page.click("#settingsBtn")
        check("challenge level hidden in Simple mode", not page.locator("#setChallenge").is_visible())
        page.evaluate("setAdv(true, true)") if page.evaluate("typeof setAdv") == "function" else None
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.14.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
