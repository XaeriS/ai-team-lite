"""Browser test of 1.6.2 screens: L60 status label after the last iteration, L68 devil's advocate card, L69 diagram card."""
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

slug = projects.create("Cyber assistant")["slug"]
projects.update(slug, phase="review", plan_approved=True, architecture_approved=True,
                iterations=[{"n": i, "name": f"Part {i}", "status": "done", "requirements": [], "after": "continue"} for i in (1, 2)])

proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
FINDINGS = [
    {"id": "f1", "kind": "objection", "current": "Anyone in the office can print, with no limit.", "title": "Value is not measured and upkeep is real",
     "detail": "For 3 people the owner could search the site and press print.", "recommendation": "Build in stages: printer control first.", "requirements": ["REQ-2"]},
    {"id": "f2", "kind": "contradiction", "current": "Jobs start as soon as they are sent.", "title": "Approval vs instant print",
     "detail": "REQ-3 says the owner approves each job.", "recommendation": "Hold every job until approved.", "requirements": ["REQ-1", "REQ-3"]},
    {"id": "f3", "kind": "assumption", "title": "One printer only", "detail": "Nothing says so.", "recommendation": "Assume one printer."}]
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url); page.wait_for_function("typeof S === 'object' && S", timeout=8000)
        page.locator("#plist li", has_text="Cyber assistant").click()
        expect(page.locator("#phase")).to_contain_text("2/2 iterations done", timeout=8000)
        # L60
        check("idle after the last iteration: Ready for your review", page.locator("#phase").inner_text().startswith("Ready for your review"),
              page.locator("#phase").inner_text())
        def working(route):   # the app reports the team working on the version checks
            resp = route.fetch(); data = resp.json()
            for p in data["projects"]:
                p["state"] = "working"
            route.fulfill(response=resp, json=data)
        page.route("**/api/state*", working)
        page.evaluate("loadState().then(updateStateP)")
        page.wait_for_timeout(300)
        check("team still working: New version check", page.locator("#phase").inner_text().startswith("New version check"),
              page.locator("#phase").inner_text())
        check("same words in the project list", "New version check" in page.locator("#plist li.sel").inner_text())
        page.screenshot(path=OUT / "v162-status.png", clip={"x": 0, "y": 0, "width": 900, "height": 200})
        # L68
        page.evaluate("ev => onEvent(ev)", {"type": "challenge", "id": "ch1", "status": "pending", "findings": FINDINGS, "slug": slug, "seq": 9e15, "ts": 1})
        card = page.locator(".card", has_text="Devil's advocate")
        f1 = card.locator(".chf[data-f=f1]")
        text = f1.inner_text()
        check("current behaviour first, then the objection, then Fred's recommendation",
              text.index("Current behaviour") < text.index("Value is not measured") < text.index("Fred recommends"), text)
        check("three buttons", f1.locator("[data-d]").all_inner_texts() == ["Keep current behaviour", "Accept Fred's recommendation", "Do something else"])
        check("assumptions keep Correct / Not correct", card.locator(".chf[data-f=f3] [data-d]").all_inner_texts() == ["Correct", "Not correct"])
        f1.locator("[data-d=recommend]").click()
        check("accepting the recommendation needs no typing", f1.locator("[data-why]").is_hidden())
        card.locator(".chf[data-f=f2] [data-d=other]").click()
        check("something else asks what instead", card.locator(".chf[data-f=f2] [data-why]").get_attribute("placeholder") == "What should be done instead?")
        check("send waits for the words", card.locator("[data-send]").is_disabled())
        card.locator(".chf[data-f=f2] [data-why]").fill("Hold jobs only for big prints")
        card.locator(".chf[data-f=f3] [data-d=confirm]").click()
        check("all decided: can send", card.locator("[data-send]").is_enabled())
        card.scroll_into_view_if_needed(); card.screenshot(path=OUT / "v162-devils-advocate.png")
        # L69
        page.evaluate("ev => onEvent(ev)", {"type": "choice", "id": "dg1", "status": "pending", "title": "Draw the overview diagram before building starts?",
                                            "options": ["Draw it", "Skip"], "hints": ["The diagrammer draws how the app will work.", "Go straight to building."],
                                            "slug": slug, "seq": 9e15 + 1, "ts": 2})
        dg = page.locator(".card", has_text="Draw the overview diagram")
        check("diagram card with Draw it / Skip", dg.locator(".acts button").all_inner_texts() == ["Draw it", "Skip"])
        dg.scroll_into_view_if_needed(); dg.screenshot(path=OUT / "v162-diagram-choice.png")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL 1.6.2 UI CHECKS PASSED")
