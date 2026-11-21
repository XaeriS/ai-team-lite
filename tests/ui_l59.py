"""Browser test of L59: a long chat opens with its latest part; earlier messages come back on request."""
import json, os, subprocess, sys, tempfile, time
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
from test_l59 import _long_chat   # noqa: E402

big = projects.create("Long chat")["slug"]; _long_chat(big, 1000)
small = projects.create("Short chat")["slug"]; _long_chat(small, 20)

proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=dict(os.environ), stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
SEL = "#msgs .m.fred"
msgs = lambda page: page.evaluate(f"[...document.querySelectorAll('{SEL}')].map(m => m.innerText)")
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url); page.wait_for_function("typeof S === 'object' && S && S.projects.length == 2", timeout=8000)
        t = time.perf_counter()
        page.locator("#plist li", has_text="Long chat").click()
        expect(page.locator("#msgs .earlier button")).to_be_visible(timeout=8000)
        took = time.perf_counter() - t
        shown = msgs(page)
        check("opens with the latest 300 messages", len(shown) == 300 and "message 999" in shown[-1], (len(shown), shown[-1:]))
        check(f"opens quickly ({took:.2f} s)", took < 1.5)
        check("the latest message is in view", page.evaluate("(() => { const b = $('msgs'); return b.scrollHeight - b.scrollTop - b.clientHeight < 5; })()"))
        page.screenshot(path=OUT / "v162-long-chat.png")
        page.evaluate("$('msgs').scrollTop = 0")
        page.click("#msgs .earlier button")
        expect(page.locator(SEL)).to_have_count(600, timeout=8000)
        got = msgs(page)
        check("earlier messages added above", "message 699" in got[299] and "message 700" in got[300] and "message 400" in got[0], got[299:301])
        y = page.evaluate("(() => { const m = [...document.querySelectorAll('#msgs .m.fred')].find(x => x.innerText.includes('message 700')); return m.getBoundingClientRect().top; })()")
        check("the message you were reading stays in place", y > 0, y)
        page.click("#msgs .earlier button"); expect(page.locator(SEL)).to_have_count(900, timeout=8000)
        page.click("#msgs .earlier button"); expect(page.locator(SEL)).to_have_count(1000, timeout=8000)
        check("button gone once everything is shown", page.locator("#msgs .earlier").count() == 0)
        check("first message reached", "message 0" in msgs(page)[0])
        page.locator("#plist li", has_text="Short chat").click()
        expect(page.locator(SEL)).to_have_count(20, timeout=8000)
        check("short chats show everything, no button", page.locator("#msgs .earlier").count() == 0)
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL L59 UI CHECKS PASSED")
