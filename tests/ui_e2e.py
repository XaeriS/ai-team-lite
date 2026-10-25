"""Drive the real UI in Chromium against demo_server.py. Saves screenshots to the folder given as argv[1]."""
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
env = {**os.environ, "AITEAM_HOME": str(tmp / "data"), "AITEAM_SECRETS_FILE": str(tmp / "secrets.json")}
proc = subprocess.Popen([sys.executable, str(HERE / "demo_server.py")], env=env, stdout=subprocess.PIPE, text=True)
url = proc.stdout.readline().strip()
failures, errors = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def through_mat(page, expect):
    """0.6.0: after the requirements, Mat (lead architect) agrees the architecture before the plan."""
    expect(page.locator(".m.mat").last).to_contain_text("I'm Mat", timeout=10000)
    page.fill("#input", "Typed once, and yes encrypt it")
    page.keyboard.press("Enter")
    arch = page.locator(".card.pending", has_text="Architecture: Expense tracker")
    expect(arch).to_be_visible(timeout=10000)
    arch.locator("text=Approve architecture").click()


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 880}, color_scheme=os.environ.get("SCHEME", "light"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(url)

        # 1. onboarding: API key
        expect(page.locator("#onboard")).to_be_visible()
        page.screenshot(path=OUT / "1-welcome.png")
        page.fill("#keyIn", "not-a-key")
        page.click("#keyBtn")
        expect(page.locator("#keyErr")).to_contain_text("sk-ant-")
        errors.clear()  # the 400 for the deliberately wrong key is expected
        page.fill("#keyIn", "sk-ant-demo")
        page.click("#keyBtn")
        expect(page.locator("#onboard")).to_be_hidden()
        check("onboarding accepted the key", True)

        # 2. setup conversation
        expect(page.locator(".m.fred").first).to_contain_text("what's your first name", timeout=10000)
        check("setup checklist shown", page.locator(".check").count() == 6)
        check("New project disabled during setup", page.locator("#newBtn").is_disabled())
        page.fill("#input", "Fred")
        page.keyboard.press("Enter")
        folder_card = page.locator(".card.pending", has_text="Where should your projects live")
        expect(folder_card).to_be_visible(timeout=10000)
        page.screenshot(path=OUT / "2-setup-folder.png")
        folder_card.locator("input").fill(str(tmp / "My Projects"))
        folder_card.locator("text=Use this folder").click()
        secret_card = page.locator(".card.pending", has_text="Paste your GitHub token")
        expect(secret_card).to_be_visible(timeout=10000)
        check("help link opens externally", secret_card.locator("a[target=_blank]").count() == 1)
        secret_card.locator("input").fill("ghp_demo_token_123")
        page.screenshot(path=OUT / "3-setup-secret.png")
        secret_card.locator("text=Save securely").click()
        expect(page.locator(".card", has_text="Setup complete")).to_be_visible(timeout=10000)
        check("secret value not displayed anywhere", "ghp_demo_token_123" not in page.content())
        page.screenshot(path=OUT / "4-setup-done.png")
        expect(page.locator("#newBtn")).to_be_enabled(timeout=5000)

        # 3. new project + requirements
        page.click("#newBtn")
        page.fill("#newName", "Expense tracker")
        page.click("#newForm button[type=submit]")
        expect(page.locator(".m.fred").first).to_contain_text("What would you like to build", timeout=10000)
        page.fill("#input", "I want to track my expenses and see monthly totals per category")
        page.keyboard.press("Enter")
        expect(page.locator(".m.fred").last).to_contain_text("desktop window", timeout=10000)
        page.locator(".tab", has_text="Requirements").click()
        expect(page.locator("#pane")).to_contain_text("REQ-1", timeout=5000)
        check("requirements panel updated live", True)
        page.screenshot(path=OUT / "5-requirements.png")

        # 4. plan approval
        page.fill("#input", "Desktop window, no Excel. Make a plan")
        page.keyboard.press("Enter")
        through_mat(page, expect)
        plan = page.locator(".card.pending", has_text="Plan: Expense tracker v1")
        expect(plan).to_be_visible(timeout=10000)
        page.screenshot(path=OUT / "6-plan.png")
        plan.locator("text=Approve plan and start").click()

        # 5. worker agent + risky approval card
        expect(page.locator(".agent", has_text="Developer")).to_be_visible(timeout=10000)
        appr = page.locator(".card.pending", has_text="Run a command")
        expect(appr).to_be_visible(timeout=15000)
        check("approval shows command", "winget install" in appr.inner_text())
        check("approval shows reason", "risky command" in appr.inner_text())
        page.screenshot(path=OUT / "7-approval.png")
        appr.locator("input").fill("No need, we use the bundled Python")
        appr.locator("button", has_text="Decline").click()
        expect(page.locator(".card", has_text="Run a command").locator(".pill.bad")).to_be_visible(timeout=5000)

        # 6. screenshot + result
        expect(page.locator(".card.shot img")).to_be_visible(timeout=20000)
        expect(page.locator(".m.fred").last).to_contain_text("starting the app", timeout=10000)
        check("developer marked done", page.locator(".agent.done", has_text="Developer").count() == 1)
        page.locator(".tab", has_text="Tests").click()
        expect(page.locator(".trow-test img").first).to_be_visible(timeout=5000)
        check("screenshot in the Tests view", page.locator(".trow-test img").count() >= 1)
        page.screenshot(path=OUT / "8-result.png")
        page.locator(".tab", has_text="Plan").click()
        check("plan tab shows plan", "Developer builds" in page.locator("#pane").inner_text())

        # 7. reload keeps the conversation
        page.reload()
        expect(page.locator(".m.user").first).to_be_visible(timeout=10000)
        check("history survives reload", page.locator(".card", has_text="Plan: Expense tracker v1").count() == 1)
        check("project listed", page.locator("#plist li", has_text="Expense tracker").count() == 1)
        b.close()
finally:
    proc.kill()

check("no JavaScript errors", not errors)
for e in errors:
    print("  JS:", e)
print("FAILED: " + ", ".join(failures) if failures else "ALL UI CHECKS PASSED")
sys.exit(1 if failures else 0)
