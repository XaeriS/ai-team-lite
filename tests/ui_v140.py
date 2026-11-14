"""Browser test of AI Team Lite 1.4.0 (L39): the window stays usable on half and on a quarter of the screen: no
sideways scrolling, the message box always in view, the project list behind the menu button when the window is narrow,
and the side panel as a slide-in."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_USAGE": "1",
       "DEMO_V100": "1"}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
SIZES = [("half of a 1080p screen", 960, 1040), ("quarter of a 1080p screen", 960, 520),
         ("quarter of a laptop screen", 683, 384), ("smallest window", 480, 400)]
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, w, h in SIZES:
            page = b.new_page(viewport={"width": w, "height": h})
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(url)
            page.wait_for_function("typeof S === 'object' && S", timeout=8000)
            narrow = w <= 760
            check(f"{name}: menu button {'shown' if narrow else 'hidden'}", page.locator("#navBtn").is_visible() == narrow)
            if narrow:
                check(f"{name}: project list out of the way", page.evaluate("document.querySelector('nav').getBoundingClientRect().right") <= 0)
                page.click("#navBtn")
                expect(page.locator("#app.navopen")).to_have_count(1)
                page.wait_for_timeout(300)
                page.screenshot(path=OUT / f"v140-{w}x{h}-menu.png")
            page.locator("#plist li", has_text="Invoice tracker").click()
            expect(page.locator(".m.fred").last).to_contain_text("Iteration 3 is under way", timeout=8000)
            page.wait_for_timeout(350)
            if narrow:
                check(f"{name}: choosing a project closes the menu", page.locator("#app.navopen").count() == 0)
            m = page.evaluate("""(() => { const r = e => document.querySelector(e).getBoundingClientRect();
              const i = r('#input'), s = r('#sendBtn'), main = r('main');
              return {sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
                      inputIn: i.top >= 0 && i.bottom <= innerHeight && i.left >= 0 && i.right <= innerWidth && i.width >= 200,
                      sendIn: s.bottom <= innerHeight && s.right <= innerWidth, mainW: main.width, mainH: main.height,
                      msgsH: r('#msgs').height, side: getComputedStyle(document.querySelector('#side')).display,
                      wide: [...document.querySelectorAll('#msgs > *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).length}; })()""")
            check(f"{name}: no sideways scrolling", m["sw"] <= m["cw"] and m["wide"] == 0, m)
            check(f"{name}: message box and Send in view", m["inputIn"] and m["sendIn"], m)
            check(f"{name}: the chat fills the window", m["mainH"] >= h - 2 and m["mainW"] >= (w if narrow else w - 221) - 2 and m["msgsH"] >= 60, m)
            check(f"{name}: side panel closed until asked for", m["side"] == "none", m)
            page.screenshot(path=OUT / f"v140-{w}x{h}.png")
            page.click("#sideToggle")
            page.wait_for_timeout(250)
            side = page.evaluate("(() => { const r = document.querySelector('#side').getBoundingClientRect(), t = document.querySelector('#sideToggle').getBoundingClientRect(); return {l: r.left, r: r.right, w: r.width, tl: t.left, tr: t.right}; })()")
            check(f"{name}: side panel slides in, its button stays reachable", side["w"] >= 300 and side["r"] <= w + 1 and side["l"] >= 0 and side["tl"] >= 0, side)
            check(f"{name}: side panel shows the team", page.locator("#side .team-svg").is_visible())
            page.screenshot(path=OUT / f"v140-{w}x{h}-side.png")
            page.click("#sideToggle")
            page.wait_for_timeout(200)
            if narrow:
                page.click("#navBtn")
            page.click("#settingsBtn")
            page.wait_for_timeout(250)
            d = page.evaluate("(() => { const r = document.querySelector('#setDlg').getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; })()")
            check(f"{name}: Settings fits the window", d["l"] >= 0 and d["r"] <= w and d["t"] >= 0 and d["b"] <= h, d)
            check(f"{name}: Save button reachable", page.locator("#setSave").is_visible())
            page.screenshot(path=OUT / f"v140-{w}x{h}-settings.png")
            page.close()
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
src = (HERE.parent / "app" / "main.py").read_text(encoding="utf-8")
check("the window may be made as small as 480 x 400", "min_size=(480, 400)" in src)
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
print("ALL 1.4.0 LAYOUT CHECKS PASSED")
