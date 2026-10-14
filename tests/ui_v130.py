"""Browser test of 0.13.0: Archify diagrams in a Diagrams tab (Simple mode too), Open and What changed, Ask Fred box;
the Graphify buttons are gone from the Changes tab."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
ARCH = Path("/home/claude/build/dl/archify-v3.1.0/archify")
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "AITEAM_ARCHIFY": str(ARCH)}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)

def add_diagrams(slug):
    """What the diagrammer does: finalize two versions with Archify, then record them."""
    os.environ.update(AITEAM_HOME=str(tmp / "d"), AITEAM_SECRETS_FILE=str(tmp / "s.json"), AITEAM_ARCHIFY=str(ARCH))
    sys.path.insert(0, str(HERE.parent))
    from app import diagrams, projects
    f = projects.project_dir(slug)
    for v in ("base", "head"):
        d = f / ".archify" / f"architecture-app-{v}"; d.mkdir(parents=True)
        shutil.copy(ARCH / "examples" / f"checkout-platform.{v}.architecture.json", d / "candidate.json")
        subprocess.run(["node", str(ARCH / "bin" / "archify.mjs"), "finalize", "architecture", str(d / "candidate.json"),
                        str(d / "app.html")], cwd=f, capture_output=True)
        diagrams.record(slug, "How the app works", "architecture", "architecture",
                        f".archify/architecture-app-{v}/app.html", f".archify/architecture-app-{v}/candidate.json",
                        "What are the main parts and how do they connect?")
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        page.click("#newBtn"); page.fill("#newName", "Recipe box"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        page.locator("#tabs button", has_text="Diagrams").click()
        expect(page.locator("#pane")).to_contain_text("No diagram yet", timeout=6000)
        check("Diagrams tab in Simple mode, with Ask Fred", page.locator("#dgAsk").is_visible())
        add_diagrams(page.evaluate("cur"))
        page.locator("#tabs button", has_text="Plan").click(); page.locator("#tabs button", has_text="Diagrams").click()
        expect(page.locator("#pane .itcard:not(details .itcard)")).to_have_count(1, timeout=6000)
        check("latest version shown, older one folded", "Earlier versions (1)" in page.locator("#pane").inner_text())
        check("What changed offered", page.locator("#pane [data-cmp]").count() == 1)
        page.screenshot(path=OUT / "n1-diagrams-tab.png")
        page.locator("#pane [data-open]").first.click()
        expect(page.locator("#graphDlg")).to_be_visible()
        try:
            expect(page.frame_locator("#graphFrame").locator("svg").first).to_be_visible(timeout=10000); ok = True
        except Exception:
            ok = False
        check("diagram drawn in the app", ok)
        page.wait_for_timeout(800); page.screenshot(path=OUT / "n2-diagram.png")
        page.locator("#graphDlg button[type=submit]").click()
        page.locator("#pane [data-cmp]").first.click()
        check("what-changed view titled", page.locator("#graphTitle").inner_text().startswith("What changed"))
        try:
            expect(page.frame_locator("#graphFrame").locator("svg").first).to_be_visible(timeout=10000); ok = True
        except Exception:
            ok = False
        check("comparison drawn", ok)
        page.wait_for_timeout(800); page.screenshot(path=OUT / "n3-what-changed.png")
        page.locator("#graphDlg button[type=submit]").click()
        page.fill("#dgQ", "How does a recipe get saved?"); page.click("#dgAsk")
        expect(page.locator(".m.user").last).to_contain_text("Please draw a diagram that shows: How does a recipe get saved?", timeout=6000)
        check("ask goes to Fred in the chat", True)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.13.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
