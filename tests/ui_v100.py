"""Browser test of AI Team Lite 1.0.0: the watchdog line and the stuck card (L8), usage in plain words with
period / agent / phase summaries, per-iteration cost and time per phase (L6, L9, L13, L14), the plan's room left
(L15), the mini chart next to the working indicator, screenshots pasted into the message box (L10), the
microphone test (L11) and the name kept out of the header (L12)."""
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
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 950}, permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(url)
        page.wait_for_function("typeof S === 'object' && S", timeout=8000)
        page.locator("#plist li", has_text="Invoice tracker").click()
        expect(page.locator(".m.fred").last).to_contain_text("Iteration 3 is under way", timeout=8000)

        # L12: the name is a greeting, not part of the product name
        check("no 'with <name>' under the product name", "with Fred" not in page.locator(".brand").inner_text(),
              page.locator(".brand").inner_text())

        # L8: the watchdog line and the stuck card
        check("watchdog line shown", page.locator(".notice.watch", has_text="Reviewer is running the full test suite").count() == 1)
        card = page.locator(".card", has_text="Nothing is moving")
        check("stuck card offers both actions", card.locator("button", has_text="Wake Fred up").count() == 1
              and card.locator("button", has_text="Stop and restart this step").count() == 1)
        check("plan room warning in the chat", page.locator(".itcard", has_text="18% of your 5-hour limit").count() == 1)
        page.screenshot(path=OUT / "v100-chat.png")
        # L28: text in the chat can be selected and copied; right-click menu in messages and in the message box
        fred = page.locator(".m.fred").last
        check("message text can be selected", page.evaluate("getComputedStyle(document.querySelector('.m.fred')).userSelect") != "none")
        check("buttons are not selectable", page.evaluate("getComputedStyle(document.querySelector('#settingsBtn')).userSelect") == "none")
        fred.hover()
        copy = fred.locator("xpath=..").locator(".meta .copy")
        copy.click()
        expect(copy).to_have_text("Copied", timeout=3000)
        check("Copy button puts the message on the clipboard",
              page.evaluate("navigator.clipboard.readText()") == fred.inner_text().strip())
        fred.click(button="right")
        menu = page.locator(".ctxmenu")
        check("right-click on a message offers Copy message", menu.locator("button", has_text="Copy message").count() == 1)
        page.screenshot(path=OUT / "v130-copy-menu.png")
        page.keyboard.press("Escape")
        check("menu closes with Escape", menu.count() == 0)
        page.evaluate("navigator.clipboard.writeText('pasted from the clipboard')")
        page.fill("#input", "Hello ")
        page.locator("#input").click(button="right")
        menu.locator("button", has_text="Paste").click()
        expect(page.locator("#input")).to_have_value("Hello pasted from the clipboard", timeout=3000)
        check("right-click Paste in the message box", True)
        page.fill("#input", "")
        # L26: an event reusing an old number (from an earlier run) is still shown; a true duplicate is not
        n = page.evaluate("""(() => { const old = items.find(x => x.type === 'message');
          const ev = {type: 'approval', id: 'l26card', status: 'pending', title: 'Run a command', who: 'Diagrammer',
                      slug: cur, seq: old.seq, ts: old.ts + 3600};
          onEvent(ev); onEvent({...ev}); return document.querySelectorAll('#msgs .card').length; })()""")
        check("card with a reused number is shown once", page.locator(".card", has_text="Run a command").count() >= 1
              and page.evaluate("items.filter(x => x.id === 'l26card').length") == 1, n)
        # L25: a limit with no known reset time says it keeps checking, and can be cancelled
        lim = page.evaluate("(() => { const n = limitCard({status: 'waiting', checking: true, auto: false, label: 'This week'}); return {t: n.innerText, c: n.querySelectorAll('[data-a=cancel]').length}; })()")
        check("limit card: checks every 15 minutes", "every 15 minutes" in lim["t"] and "this week limit" in lim["t"] and lim["c"] == 1, lim)

        # L6 / L9 / L13 / L14 / L15: the usage window
        page.click("#usageMeter")
        expect(page.locator("#useBody")).to_contain_text("Invoice tracker", timeout=8000)
        body = page.locator("#useBody").inner_text()
        check("headline: what the prototype would cost over its finished iterations",
              "would cost" in body and "over 2 finished iterations" in body, body[:300])
        check("subscription: clearly not billed", "Not billed" in body)
        check("plan room left from Claude", "5-hour limit" in body and "54% left" in body and "read from Claude" in body, body[:600])
        check("rest of the plan estimated", "The rest of the plan" in body)
        check("live activity charts", page.locator("#useBody .chartbox svg").count() == 2)
        check("per iteration table", page.locator("#useBody tr[data-it]").count() >= 4)
        check("time per phase with waiting apart", "Team working" in body and "Waiting for you" in body and "Build" in body)
        page.screenshot(path=OUT / "v100-usage.png", full_page=True)
        page.locator("#useBody").screenshot(path=OUT / "v100-usage-body.png")
        # split by phase, then by month
        page.click("#useBody [data-split=stage]")
        check("split by phase", page.locator("#useBody th", has_text="Phase").count() >= 1
              and "Requirements" in page.locator("#useBody").inner_text())
        page.click("#useBody [data-unit=month]")
        expect(page.locator("#useBody")).to_contain_text("This month", timeout=5000)
        page.locator("#useBody tr[data-period]").first.click()
        check("a period can be split on its own", "split by" in page.locator("#useBody").inner_text()
              and page.locator("#useBody tr.sel[data-period]").count() == 1)
        page.locator("#useBody tr[data-it='2']").click()
        txt = page.locator("#useBody").inner_text()
        check("iteration detail: agents, phases and time", "By agent" in txt and "Time by phase" in txt and "Developer" in txt)
        page.locator("#useBody").screenshot(path=OUT / "v100-usage-detail.png")
        page.click("#useBody [data-scope='']")
        expect(page.locator("#useBody")).to_contain_text("Per project", timeout=5000)
        check("all projects: month headline", "This month the team has used" in page.locator("#useBody").inner_text())
        page.locator("#useDlg button[type=submit]").click()

        # L5: why Fred asked, and the questions that changed the project
        check("problem card: why Fred asks each question", page.locator(".card", has_text="The problem we are solving")
              .locator("details.why").count() == 5)
        ch = page.locator(".card", has_text="Devil's advocate")
        ch.locator("details.why summary").first.click()
        check("devil's advocate point explains why it was asked", "most common reason a tool fails" in ch.inner_text())
        q = page.locator(".card", has_text="The questions that changed your project")
        check("questions summary card", "Invoices read automatically" in q.inner_text() and "QUESTIONS.md" in q.inner_text())

        # L10: paste a screenshot and drop a file into the message box
        page.evaluate("""() => {
          const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
          const dt = new DataTransfer(); dt.items.add(new File([bytes], 'image.png', {type: 'image/png'}));
          document.getElementById('input').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
        }""")
        expect(page.locator("#chips .chip.img img.thumb")).to_have_count(1, timeout=5000)
        check("pasted screenshot saved in inputs/ with a clear name",
              page.locator("#chips .chip").first.get_attribute("data-path").startswith("inputs/screenshot-"))
        page.evaluate("""() => {
          const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
          const dt = new DataTransfer(); dt.items.add(new File([bytes], 'error-dialog.png', {type: 'image/png'}));
          document.querySelector('.cbox').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
        }""")
        expect(page.locator("#chips .chip.img")).to_have_count(2, timeout=5000)
        check("dropped image shown as a thumbnail chip", page.locator("#chips .chip[data-path='inputs/error-dialog.png']").count() == 1)
        page.screenshot(path=OUT / "v100-paste.png")

        # L11 + L12: microphone test and the name in Settings > General
        page.click("#settingsBtn")
        check("name editable in Settings > General", page.locator("#setName").input_value() == "Fred")
        page.click("#lessonsBox summary")
        expect(page.locator("#lessonsList")).to_contain_text("bundled Python", timeout=5000)
        check("lessons listed in Settings", "(1)" in page.locator("#lessonsCount").inner_text())
        page.locator("#lessonsList [data-del]").first.click()
        expect(page.locator("#lessonsList")).to_contain_text("Nothing learned yet", timeout=5000)
        check("a lesson can be deleted", "(none yet)" in page.locator("#lessonsCount").inner_text())
        page.click("#setMicTest")
        expect(page.locator("#micResult")).to_contain_text("The microphone works", timeout=8000)
        check("device name shown", "Demo USB microphone" in page.locator("#micDev").inner_text())
        check("link to Windows microphone settings", page.locator("#micPrivacy").count() == 1)
        page.screenshot(path=OUT / "v100-mic.png")
        page.locator("#micDlg button[type=submit]").click()
        # L17: approvals from the phone
        check("answer from the phone is labelled", page.locator(".card", has_text="Run a command")
              .locator(".pill", has_text="from your phone").count() == 1)
        page.click(".stab[data-s=setup]")
        box = page.locator("#phoneBox")
        check("phone approvals explained in Settings", "Nothing is installed on this PC" in box.inner_text()
              and box.locator("#phoneSetup").count() == 1)
        page.screenshot(path=OUT / "v110-settings-phone.png")
        page.click(".stab[data-s=gen]")
        page.fill("#setName", "Anne"); page.click("#setSave")
        page.wait_for_timeout(500)
        check("name saved", page.evaluate("S.settings.user_name") == "Anne")
        page.evaluate("open('setup')")
        expect(page.locator(".card", has_text="Link your phone")).to_contain_text("482913", timeout=8000)
        check("link card shows the bot and the code", "@my_ai_team_bot" in page.locator(".card", has_text="Link your phone").inner_text())
        page.screenshot(path=OUT / "v110-link.png")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs, errs[:3])
if fails:
    print("FAILED:", ", ".join(fails)); sys.exit(1)
