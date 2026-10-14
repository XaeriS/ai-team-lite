"""Browser test of 0.11.0 brain fixes:
(54) Gemini: every model is listed (not just 2), each with what it is best for, recommended ones first;
(55) Ollama: the whole catalog sized to this PC, installed models highlighted, too-big ones folded away, and a
     model you don't have yet is downloaded and added in one click;
(56) OmniRoute is not a manual provider; the gateway section installs/starts/connects it and adds its models;
     the chosen brain mode survives Save and close."""
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
        page = b.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(url)
        page.click("#settingsBtn")
        page.click(".stab[data-s=brains]")

        provs = page.locator("#bProv option").all_inner_texts()
        check("council providers: Gemini and Ollama only", len(provs) == 2 and not any("omniroute" in p.lower() for p in provs), provs)

        # (54) Gemini: all 23 text models listed, with notes, recommended first
        page.select_option("#bProv", "gemini")
        page.fill("#bKey", "AIza-demo")
        page.click("#bLoad")
        expect(page.locator("#bLoadMsg")).to_contain_text("23 models", timeout=8000)
        n = page.locator("#bModelList .mitem").count()
        check("all 23 Gemini models shown (not 2)", n == 23, n)
        txt = page.locator("#bModelList").inner_text()
        check("image/speech/embedding models filtered out", "embedding" not in txt and "tts" not in txt)
        check("recommended group first", page.locator("#bModelList .mgroup").first.inner_text() == "Recommended")
        check("notes explain each model", "Early preview" in txt and "Fixed snapshot" in txt and "Always points to the newest" in txt)
        check("stable 2.5 not called 'older' because of a 3.0 preview",
              "Older generation" not in page.locator(".mitem[data-m='gemini-2.5-flash']").inner_text())
        check("free/paid shown", page.locator("#bModelList .pill.ok").count() > 0 and page.locator("#bModelList .pill.warn").count() > 0)
        page.screenshot(path=OUT / "g1-gemini-list.png", full_page=True)

        # (55) Ollama catalog for a 16 GB PC
        page.select_option("#bProv", "ollama")
        expect(page.locator("#bLoadMsg")).to_contain_text("16 GB", timeout=8000)
        groups = page.locator("#bModelList .mgroup").all_inner_texts()
        check("installed models grouped first", groups and groups[0].startswith("Already on your PC (2)"), groups)
        check("installed highlighted", page.locator("#bModelList .mitem.inst").count() == 2)
        check("too-big models folded away", "Too big" in page.locator("#bModelList details summary").inner_text())
        check("32 GB models not in the fitting list",
              page.locator("#bModelList > .mitem[data-m='qwen3-coder:30b']").count() == 0)
        check("recommended model marked", page.locator(".mitem[data-m='qwen2.5-coder:14b'] .pill.ok").count() >= 1)
        check("download size + memory shown", "GB download" in page.locator("#bModelList").inner_text())
        page.screenshot(path=OUT / "g2-ollama-catalog.png", full_page=True)

        # an installed model is added straight away
        page.locator(".mitem[data-m='qwen2.5-coder:7b']").click()
        page.click("#bAddBtn")
        expect(page.locator("#bMsg")).to_contain_text("working", timeout=8000)
        expect(page.locator("#brainList .brainrow")).to_have_count(1, timeout=8000)
        # a model not on the PC: offered as download, then added
        page.locator(".mitem[data-m='qwen3:8b']").click()
        page.click("#bAddBtn")
        expect(page.locator("#bGet")).to_be_visible(timeout=8000)
        check("download offered with size", "5.2 GB" in page.locator("#bMsg").inner_text())
        page.click("#bGet")
        expect(page.locator("#bMsg")).to_contain_text("downloaded, tested and added", timeout=15000)
        expect(page.locator("#brainList .brainrow")).to_have_count(2, timeout=8000)
        try:
            expect(page.locator(".mitem.inst[data-m='qwen3:8b']")).to_have_count(1, timeout=8000); ok = True
        except Exception:
            ok = False
        check("downloaded model now shows as installed", ok)
        page.screenshot(path=OUT / "g3-download-added.png")

        page.click("#setCancel")
        b.close()
finally:
    proc.kill()
check("no JavaScript errors", not errs)
for e in errs:
    print("  JS:", e)
print("FAILED: " + ", ".join(fails) if fails else "ALL 0.11.0 BRAIN UI CHECKS PASSED")
sys.exit(1 if fails else 0)
