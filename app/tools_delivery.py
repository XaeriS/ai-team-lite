"""Tools for delivering a finished project (L52b-d): a real Windows installer, automatic tests on GitHub, and a
GitHub release with the installer attached."""
from __future__ import annotations

from typing import TYPE_CHECKING

from claude_agent_sdk import tool

from . import github, iterations, packaging, paths, projects, shipping
from .tools_common import text

if TYPE_CHECKING:
    from .session import Session


def make(session: "Session") -> dict:
    slug = session.slug

    @tool("package_app", "When a version is ready and the user wants an app to install or share: build a real "
          "Windows installer (Start-menu entry, the app's icon, an optional desktop shortcut chosen during setup, an "
          "uninstaller), installed for the current user without administrator rights. entry = the start file if not "
          "main.py/app.py; windowed = true for apps with their own window (no console); version (default 1.<iterations "
          "done>). Tells you what to install first if PyInstaller or Inno Setup is missing.",
          {"entry": str, "windowed": bool, "version": str})
    async def package_app(args):
        if not paths.IS_WINDOWS:
            return text("Packaging makes a Windows installer and runs on Windows only.", True)
        missing = await session.run_blocking(packaging.needs, slug)
        if missing:
            return text("Before packaging, these must be installed (each install asks the user; explain why first: "
                        "PyInstaller bundles the app with its own Python, Inno Setup makes the setup program):\n- "
                        + "\n- ".join(missing) + "\nThen call package_app again.", True)
        session.set_role("Developer", status="working", task="Packaging the app as an installer")
        try:
            info = await session.run_blocking(lambda: packaging.build(
                slug, entry=str(args.get("entry") or ""), version=str(args.get("version") or ""),
                windowed=args["windowed"] if isinstance(args.get("windowed"), bool) else None))
        except packaging.PackagingError as e:
            return text(f"Packaging failed: {e}", True)
        finally:
            session.set_role("Developer", status="idle", task="")
        session.hub.publish(slug, {"type": "package", **info})
        mb = info["bytes"] / 1_048_576
        return text(f"Installer ready: {info['installer']} ({mb:.0f} MB, version {info['version']}). The user sees it in "
                    "the chat. Tell them in two sentences: double-click to install (Windows may warn that it does not "
                    "know the publisher, because the installer is not signed: More info, then Run anyway), and they "
                    "can share this one file. Offer to publish it on GitHub (github_release) if the project has a "
                    "repository.")

    @tool("add_ci", "Add automatic tests on GitHub (GitHub Actions, on Windows): every push runs the project's tests "
          "there too. Writes .github/workflows/tests.yml; push it with github_push.", {})
    async def add_ci(args):
        out = shipping.write_ci(slug)
        note = ""
        try:
            if projects.load(slug).get("github_repo") and github.needs_workflow_scope():
                note = (" Note: the saved GitHub key cannot upload workflow files (it lacks the 'workflow' permission). "
                        "Before pushing, explain this in one sentence and ask the user for a new key with request_secret "
                        "(name github_token), created at https://github.com/settings/tokens/new?scopes=repo,workflow"
                        "&description=AI%20Team%20Lite")
        except github.GitHubError:
            pass
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        return text(f"Wrote {out.relative_to(projects.project_dir(slug)).as_posix()} (runs: "
                    f"{shipping.ci_command(slug)}). Commit it with the next iteration or the clean-up, then push." + note)

    @tool("github_release", "Publish the installer made by package_app on the project's GitHub repository as a "
          "release (asks the user first). Push the code first.", {"notes": str, "user_asked_now": bool})
    async def github_release(args):
        p = projects.load(slug) or {}
        pkg = p.get("package") or {}
        if not p.get("github_repo"):
            return text("This project has no GitHub repository yet. Use github_create_repo first.", True)
        setup = projects.project_dir(slug) / str(pkg.get("installer") or "_none_")
        if not setup.is_file():
            return text("There is no installer yet: call package_app first.", True)
        wait = "" if args.get("user_asked_now") else iterations.cleanup_before_push(slug)   # L70
        if wait:
            return text(wait, True)
        tag = f"v{pkg['version']}"
        try:
            url = await session.run_blocking(github.create_release, p["github_repo"], tag, f"{pkg['name']} {pkg['version']}",
                                             str(args.get("notes") or "").strip() or f"{pkg['name']} {pkg['version']}.", setup)
        except github.GitHubError as e:
            return text(f"Publishing failed: {e}", True)
        projects.update(slug, package={**pkg, "release": url, "tag": tag})
        session.hub.publish(slug, {"type": "notice", "text": f"Published {pkg['name']} {pkg['version']} on GitHub: {url}"})
        return text(f"Published as {tag}: {url}. Give the user this link: anyone with access to the repository can "
                    "download the installer from it.")

    return {"package_app": package_app, "add_ci": add_ci, "github_release": github_release}
