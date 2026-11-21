"""Traceability: every requirement (REQ-1..REQ-21) is mapped to tests, or listed as unproven with a reason."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "requirements_map.json"
STATUS = ROOT / "STATUS.md"
ALL_REQS = [f"REQ-{n}" for n in range(1, 22)]
SECTION_TITLE = "Requirements not proven by a test"


def _load():
    assert DATA.exists(), "tests/requirements_map.json does not exist"
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_requirements_map_file_exists_and_is_valid_json():
    data = _load()
    assert isinstance(data, dict)
    assert isinstance(data.get("map"), dict)
    assert isinstance(data.get("unproven"), dict)


def test_every_requirement_is_in_exactly_one_of_map_or_unproven():
    data = _load()
    for req in ALL_REQS:
        places = (req in data["map"]) + (req in data["unproven"])
        assert places == 1, f"{req} appears in {places} of map/unproven (must be exactly 1)"


def test_map_and_unproven_only_contain_known_requirements():
    data = _load()
    unknown = (set(data["map"]) | set(data["unproven"])) - set(ALL_REQS)
    assert not unknown, f"unknown requirement ids: {sorted(unknown)}"


def test_every_mapped_requirement_lists_existing_test_files():
    data = _load()
    for req, files in data["map"].items():
        assert isinstance(files, list) and files, f"{req} has no test files listed"
        for f in files:
            p = (ROOT / f).resolve()
            assert ROOT in p.parents, f"{req}: {f} is outside the project"
            assert p.is_file(), f"{req}: {f} does not exist"


def test_every_unproven_requirement_has_a_non_empty_reason():
    data = _load()
    for req, reason in data["unproven"].items():
        assert isinstance(reason, str) and reason.strip(), f"{req} has no reason"


def _status_section():
    assert STATUS.exists(), "STATUS.md does not exist"
    text = STATUS.read_text(encoding="utf-8")
    m = re.search(rf"^#+\s*{re.escape(SECTION_TITLE)}\s*$(.*?)(?=^#+\s|\Z)", text, re.M | re.S | re.I)
    assert m, f'STATUS.md has no section titled "{SECTION_TITLE}"'
    return m.group(1)


def test_status_section_names_every_unproven_requirement():
    data = _load()
    section = _status_section()
    for req in data["unproven"]:
        assert re.search(rf"\b{re.escape(req)}\b(?!\d)", section), f"{req} missing from STATUS.md section"


def test_status_section_names_no_mapped_requirement():
    data = _load()
    section = _status_section()
    for req in data["map"]:
        assert not re.search(rf"\b{re.escape(req)}\b(?!\d)", section), f"{req} is mapped but named in STATUS.md section"
