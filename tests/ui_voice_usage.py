"""Browser test of talking (simulated microphone), the usage meter/tracker and the voice settings."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
(tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
                                                     "subscription_signed_in": True, "user_name": "Fred", "advanced_mode": True,
                                                     "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_VOICE": "1"}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 880})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        page.click("#newBtn")
        page.fill("#newName", "Notes app")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").first).to_contain_text("What would you like to build", timeout=10000)
        expect(page.locator("#usageMeter")).to_contain_text("Session (5 h) 23%", timeout=5000)
        check("sidebar meter shows plan usage", "Week 41%" in page.locator("#usageMeter").inner_text())
        cost_txt = page.locator("#cost").inner_text()
        check("header shows plan usage, and money only as an API-price equivalent", "Session 23%" in cost_txt
              and ("$" not in cost_txt or "at API prices" in cost_txt))
        check("icons are vector drawings", page.locator("#attachBtn svg").count() == 1 and page.locator("#micBtn svg").count() == 1)

        # talking: first use asks to download the speech model
        page.click("#micBtn")
        expect(page.locator("#voiceDlg")).to_be_visible()
        page.screenshot(path=OUT / "v1-voice-download.png")
        page.click("#vDl")
        expect(page.locator("#micBtn.on")).to_be_visible(timeout=10000)
        expect(page.locator("#voiceBar")).to_contain_text("copy")
        page.wait_for_timeout(900)
        page.screenshot(path=OUT / "v2-listening.png")
        expect(page.locator(".m.user").last).to_have_text("Can you add a dark mode to the app", timeout=10000)
        check("spoken text sent without the end word", True)
        check("mic turned off after sending", page.locator("#micBtn.on").count() == 0)
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        # auto-listen: after Fred answers, it listens again
        expect(page.locator("#micBtn.on")).to_be_visible(timeout=8000)
        check("listens again after Fred answers", True)
        page.click("#vCancel")
        expect(page.locator("#micBtn.on")).to_have_count(0, timeout=5000)

        # usage tracker
        page.click("#usageMeter")
        expect(page.locator("#useDlg")).to_be_visible()
        body = page.locator("#useBody").inner_text()
        check("tracker shows week and session in plain words", "Weekly limit" in body and "5-hour limit" in body)
        check("tracker says nothing is billed", "Not billed" in body)
        page.screenshot(path=OUT / "v3-usage.png")
        page.locator("#useDlg button[type=submit]").click()

        # voice runs with its defaults (no Talking tab in Lite)
        page.click("#settingsBtn")
        check("no Talking tab", page.locator(".stab[data-s=talk]").count() == 0)
        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL VOICE/USAGE UI CHECKS PASSED")
sys.exit(1 if fails else 0)
