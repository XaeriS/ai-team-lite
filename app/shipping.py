"""L52c: automatic tests on GitHub for a project the team built (GitHub Actions, on Windows like the user's PC)."""
from __future__ import annotations

import re
from pathlib import Path

from . import iterations, projects

WORKFLOW = Path(".github") / "workflows" / "tests.yml"


def ci_command(slug: str) -> str:
    """The test command of the last saved iteration, rewritten for GitHub's machine (plain `python`)."""
    done = [it for it in iterations.load(slug) if it.get("status") == "done" and it.get("test_command")]
    cmd = done[-1]["test_command"] if done else "python -m pytest -q"
    cmd = re.sub(r"""(?i)^\s*(["'][^"']*python(3)?(\.exe)?["']|\S*[\\/]python(3)?(\.exe)?)(?=\s)""", "python", cmd)
    return cmd.strip() or "python -m pytest -q"


def workflow_text(test_command: str, name: str) -> str:
    return f"""# Made by AI Team Lite: runs the tests of {name} on GitHub for every change (L52c).
name: Tests
on:
  push:
  pull_request:
  workflow_dispatch:
jobs:
  tests:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install what the app needs
        run: |
          python -m pip install --upgrade pip
          if (Test-Path requirements.txt) {{ pip install -r requirements.txt }}
          pip install pytest
      - name: Run the tests
        run: {test_command}
"""


def write_ci(slug: str) -> Path:
    p = projects.load(slug) or {}
    folder = projects.project_dir(slug)
    out = folder / WORKFLOW
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(workflow_text(ci_command(slug), p.get("name") or slug), encoding="utf-8")
    return out
