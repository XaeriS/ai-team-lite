"""Browser test of L49: the quality scorecard in the chat and in the Tests tab (wide and narrow window), and its
switch in Settings."""
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
from app import projects, scorecard, upkeep   # noqa: E402
from app.events import EventHub   # noqa: E402
from test_scorecard import _good, FAKE_KEY   # noqa: E402
from app.tools_common import add_test_result   # noqa: E402

slug, folder = _good("Invoices")
add_test_result(folder, {"name": "monthly totals", "status": "failed", "requirement": "REQ-2"})
(folder / "app" / "config.py").write_text(f'KEY = "{FAKE_KEY}"\n')
(folder / "requirements.txt").write_text("requests==2.32.3\nflask>=2\n")
upkeep.record_finding(slug, "Log file grows forever", "low")
card = scorecard.build(slug, online=True, fetch=lambda u, b: {"results": [{"vulns": [{"id": "GHSA-1111"}]}, {}]})
EventHub(projects.history_file).publish(slug, {"type": "scorecard", **card})
EventHub(projects.history_file).publish(slug, {"type": "approval", "id": "gh1", "status": "approved", "title": "Save to GitHub",
    "tool": "mcp__aiteam__github_push", "quality": scorecard.push_warning(slug)})
other = projects.create("Fresh")["slug"]

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
        chat = page.locator("#msgs .sc-card")
        expect(chat).to_have_count(1, timeout=8000)
        text = chat.inner_text()
        check("chat card has a plain headline", "problems to fix before this version is saved" in text, text[:200])
        check("seven checks, each in plain words", chat.locator(".sc-row").count() == 7 and "Every requirement proven by a test" in text
              and "Packages the app uses" in text and "No secrets in the files" in text)
        check("each check says OK / To look at / To fix", all(w in text for w in ("To fix", "To look at", "OK")))
        check("red mark on the failing parts", chat.locator(".sc-row[data-k=secrets] .sc-dot.problem").count() == 1
              and chat.locator(".sc-row[data-k=packages] .sc-dot.problem").count() == 1)
        check("the key itself is never shown", FAKE_KEY not in page.content())
        row = chat.locator(".sc-row[data-k=packages]")
        row.locator("summary").click()
        expect(row.locator(".sc-det")).to_contain_text("GHSA-1111", timeout=4000)
        check("details on request", "flask has no fixed version" in row.locator(".sc-det").inner_text())
        gh = page.locator("#msgs .card", has_text="Save to GitHub")
        check("GitHub card warns about the points to fix", "Quality check: 3 points to fix" in gh.inner_text(), gh.inner_text()[:300])
        chat.scroll_into_view_if_needed()
        page.screenshot(path=OUT / "v160-scorecard-chat.png")
        page.click(".tab[data-t=tests]")
        box = page.locator("#pane .sc-box")
        expect(box).to_have_count(1, timeout=8000)
        check("Tests tab starts with the quality block", "Quality of this version" in box.inner_text() and box.locator(".sc-row").count() == 7)
        check("test results still listed below", page.locator("#pane .trow-test").count() >= 2)
        page.screenshot(path=OUT / "v160-scorecard-tests.png")
        # a project not checked yet
        page.locator("#plist li", has_text="Fresh").click()
        page.click(".tab[data-t=tests]")
        expect(page.locator("#pane .sc-box")).to_contain_text("Not checked yet", timeout=8000)
        # Settings switch
        page.click("#settingsBtn")
        check("switch on by default", page.locator("#setScoreOnline").is_checked())
        check("says what is sent", "Only the package names and versions are sent to osv.dev" in page.locator("#setDlg").inner_text())
        page.locator("#setScoreOnline").uncheck()
        page.click("#setSave")
        page.wait_for_timeout(500)
        saved = json.loads((tmp / "d" / "settings.json").read_text())
        check("switch saved", saved.get("scorecard_online") is False, saved.get("scorecard_online"))
        # narrow window (a quarter of the screen)
        page.set_viewport_size({"width": 480, "height": 800})
        page.locator("#navBtn").click() if page.locator("#navBtn").is_visible() else None
        page.locator("#plist li", has_text="Invoices").click()
        expect(page.locator("#msgs .sc-card")).to_have_count(1, timeout=8000)
        c = page.locator("#msgs .sc-card")
        c.scroll_into_view_if_needed()
        page.wait_for_timeout(500)   # let the project list finish sliding away
        w = page.evaluate("(() => { const e = document.querySelector('#msgs .sc-card'); return [e.scrollWidth, e.clientWidth, document.documentElement.scrollWidth]; })()")
        check("fits a narrow window", w[0] <= w[1] + 1 and w[2] <= 481, w)
        page.screenshot(path=OUT / "v160-scorecard-narrow.png")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL SCORECARD UI CHECKS PASSED")
