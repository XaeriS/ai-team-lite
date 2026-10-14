"""How the team builds: Python with the bundled interpreter (AI Team Lite builds in Python only)."""
from __future__ import annotations

from . import paths

CONVENTIONS = """- Python with the bundled interpreter: {python}. Per-project virtual environment .venv created with it
  (`& "{python}" -m venv .venv`); packages listed in requirements.txt.
- Code lives in the project folder; entry point main.py; a run.bat that starts the app by double-click.
- Tests in tests/ using unittest or pytest, run with the project's .venv (e.g. `.venv\\Scripts\\python -m pytest -q`)."""


def conventions() -> str:
    return CONVENTIONS.replace("{python}", str(paths.bundled_python()))

