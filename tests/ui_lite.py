"""Browser test of AI Team Lite's screens: one simple layout (no Advanced switch), no expert tabs or controls,
the council brains, General settings saved, Link an existing repository in the share box."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots"); OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "d").mkdir()
(tmp / "d" / "settings.json").write_text(json.dumps({"setup_complete": True, "auth_mode": "subscription",
    "subscription_signed_in": True, "user_name": "Fred", "workspace_dir": str(tmp / "ws")}))
env = {**os.environ, "AITEAM_HOME": str(tmp / "d"), "AITEAM_SECRETS_FILE": str(tmp / "s.json"), "DEMO_USAGE": "1"}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
fails, errs = [], []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)
vis = lambda page, sel: page.locator(sel).first.is_visible()
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(url)
        page.wait_for_function("typeof S === 'object' && S", timeout=8000)
        check("no Simple/Advanced switch: one layout", not page.evaluate("document.body.classList.contains('simple')")
              and page.locator("#setAdv").count() == 0)
        check("Pipeline offered", vis(page, "#workBtn"))
        check("feedback and requested features there", vis(page, "#feedbackBtn") and vis(page, "#featuresBtn"))
        page.click("#newBtn"); page.fill("#newName", "Recipe box"); page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").last).to_contain_text("What would you like to build", timeout=10000)
        tabs = page.locator("#tabs button").all_inner_texts()
        check("tabs: Team, Requirements, Architecture, Plan, Diagrams, Tests (no Changes)",
              "Changes" not in tabs and all(t in tabs for t in ("Requirements", "Plan", "Diagrams", "Tests")), tabs)
        check("no clean-up button", page.locator("#cleanBtn").count() == 0)
        page.click("#usageMeter")
        expect(page.locator("#useBody")).to_contain_text("Weekly limit", timeout=8000)
        check("usage in plain words (1.0.0: plan room left, then simple tables)", "% left" in page.locator("#useBody").inner_text()
              and "Not billed" in page.locator("#useBody").inner_text())
        check("no dollar amount in the header", "$" not in page.locator("#cost").inner_text())
        page.screenshot(path=OUT / "m1-lite-usage.png")
        page.locator("#useDlg button[type=submit]").click()

        page.click("#settingsBtn")
        check("three settings tabs", page.locator(".stab").all_inner_texts() == ["General", "Council", "Setup & connections"],
              page.locator(".stab").all_inner_texts())
        check("plain approval wording", "Only for big steps" in page.locator("#setMode").inner_text())
        check("pipeline options in General", vis(page, "#setPar") and vis(page, "#setPipeAuto"))
        page.screenshot(path=OUT / "m2-lite-settings.png")
        page.click(".stab[data-s=brains]")
        expect(page.locator("#bProv option")).to_have_count(2, timeout=6000)
        provs = page.locator("#bProv option").all_inner_texts()
        check("council: only the free providers", len(provs) == 2 and not any("Grok" in p for p in provs), provs)
        check("no model, gateway or price controls", page.locator("#claudeList, #roleBox, #bmodeOmniLbl, #priceTable").count() == 0)
        page.screenshot(path=OUT / "m3-lite-council.png")
        page.click(".stab[data-s=gen]")
        page.select_option("#setPar", "3"); page.check("#setIterPause")
        page.click("#setSave"); page.wait_for_timeout(600)
        page.click("#settingsBtn")
        check("General choices saved", page.locator("#setPar").input_value() == "3" and page.locator("#setIterPause").is_checked())
        page.click(".stab[data-s=setup]")
        expect(page.locator("#shareBox")).to_contain_text("Create a new repository", timeout=6000)
        check("link an existing repository offered, with the Lite example", vis(page, "#shareLinkRepo")
              and "ai-team-lite" in page.locator("#shareLinkRepo").get_attribute("placeholder"))
        page.screenshot(path=OUT / "m4-share-link.png")
        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs: print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL LITE UI CHECKS PASSED")
sys.exit(1 if fails else 0)
