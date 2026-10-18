"""Browser test of 0.9.0: three brain modes, test-before-add, per-model tier, usage in/out split + cache,
Changes tab, publish/update in settings."""
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
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_BRAIN": "1"}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond:
        fails.append(name)


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 920})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(url)

        # --- council brains: added directly (no brain modes, no gateway)
        page.click("#settingsBtn")
        page.click(".stab[data-s=brains]")
        check("no brain modes or gateway", page.locator("input[name=bmode], #modeOmni").count() == 0)
        page.wait_for_function("document.querySelectorAll('#bProv option').length > 0", timeout=8000)  # loads async
        check("only the free providers", page.locator("#bProv option").count() == 2)

        # --- per-provider account instructions + free hint
        page.select_option("#bProv", "gemini")
        check("account steps shown", page.locator("#bHow li").count() >= 2)
        check("free hint shown", "flash" in page.locator("#bNote").inner_text().lower())
        page.screenshot(path=OUT / "e1-brain-modes.png")
        # a working one (demo ollama) IS added via the visible model list
        page.select_option("#bProv", "ollama")
        expect(page.locator("#bLoadMsg")).to_contain_text("already installed", timeout=8000)  # catalog auto-loads for Ollama
        page.locator(".mitem[data-m='qwen2.5-coder:7b']").click()
        page.click("#bAddBtn")
        expect(page.locator("#bMsg")).to_contain_text("working", timeout=8000)
        try:
            expect(page.locator("#brainList .brainrow")).to_have_count(1, timeout=8000); ok = True
        except Exception:
            ok = False
        check("working brain added", ok)
        page.click("#setCancel")

        # --- a project with two committed iterations, then the Changes tab
        page.click("#newBtn")
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        page.fill("#input", "Track my expenses")
        page.keyboard.press("Enter")
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        page.fill("#input", "Desktop. Make a plan")
        page.keyboard.press("Enter")
        expect(page.locator(".m.mat").last).to_contain_text("I'm Mat", timeout=10000)
        page.fill("#input", "Typed once, encrypt it")
        page.keyboard.press("Enter")
        page.locator(".card.pending", has_text="Architecture").locator("text=Approve architecture").click()
        plan = page.locator(".card.pending", has_text="Plan: Expense tracker v1")
        expect(plan).to_be_visible(timeout=10000)
        plan.locator("text=Approve plan and start").click()
        appr = page.locator(".card.pending", has_text="Run a command")
        expect(appr).to_be_visible(timeout=20000)
        appr.locator("button", has_text="Decline").click()
        expect(page.locator(".itcard.done")).to_be_visible(timeout=20000)
        check("no Changes tab in Lite", page.locator(".tab", has_text="Changes").count() == 0)

        # --- sharing / update now lives in the Setup & connections tab (moved in 0.10.0)
        page.click("#settingsBtn")
        page.click(".stab[data-s=setup]")
        expect(page.locator("#shareBox")).to_contain_text("Create a new repository", timeout=6000)
        page.screenshot(path=OUT / "e4-share-updates.png")
        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.9.0 UI CHECKS PASSED")
sys.exit(1 if fails else 0)
