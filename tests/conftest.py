import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEAM_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AITEAM_SECRETS_FILE", str(tmp_path / "secrets.json"))
    from app import settings

    settings.save({"workspace_dir": str(tmp_path / "ws"), "pipeline_auto": False, "version_cleanup": False,
                   "tests_first": False, "light_cleanup": False, "prior_art": False})
    from app import usage
    import time as _time
    # Tests never call the real Claude tool for plan figures by accident (it takes seconds and made tests flaky);
    # a test that wants it resets this to 0.
    usage._last_failed["t"] = _time.time()
    yield tmp_path
