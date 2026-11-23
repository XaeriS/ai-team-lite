"""M-5: a phone approval card shows what is being approved. M-4: Claude's engine and the test / Launch programs
do not share one writable folder."""
from pathlib import Path
from types import SimpleNamespace

from app import engine_text, risk, sandbox, telegram

ROOT = Path("C:/proj/demo")


def _card(tool, inp, rk=None):
    """The card as the app builds it (engine_text.approval_card + risk.explain), as the phone would get it."""
    card = engine_text.approval_card(tool, inp, SimpleNamespace(), ROOT)
    card["who"] = "Fred"
    card.update(risk.explain(tool, inp, ROOT, lambda p: str(p).replace("\\", "/").startswith("C:/proj/demo"),
                             [str(ROOT)]))
    if rk:
        card["risk"] = rk
    return card


def _phone(card):
    return telegram.card_message("approval", card, "Demo", False)


# ------------------------------------------------------------------ M-5: the phone shows the command / file
def test_phone_card_for_a_command_shows_the_command():
    text, buttons = _phone(_card("Bash", {"command": "python build.py --out dist"}))
    assert "python build.py --out dist" in text
    assert buttons


def test_phone_card_for_a_file_change_shows_the_file_path():
    text, _ = _phone(_card("Write", {"file_path": str(ROOT / "src" / "app.py"), "content": "print(1)\n"}))
    assert "src/app.py" in text.replace("\\", "/")


def test_phone_card_for_an_edit_shows_the_file_path():
    text, _ = _phone(_card("Edit", {"file_path": str(ROOT / "src" / "util.py"), "old_string": "a", "new_string": "b"}))
    assert "src/util.py" in text.replace("\\", "/")


def test_phone_card_shortens_a_long_command():
    long_cmd = "python x.py " + "a" * 600
    text, _ = _phone(_card("Bash", {"command": long_cmd}))
    assert "python x.py" in text
    assert "a" * 300 not in text


def test_phone_card_never_shows_a_secret_inside_the_command():
    # built at run time: written out whole, these fake keys would make the app's own pre-publish secret scan refuse
    cmd = ('curl -H "Authorization: Bearer sk-' + 'ant-api03-SECRETSECRET1234567890" '
           '-H "X-T: gh' + 'p_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" https://x.example/api')
    text, _ = _phone(_card("Bash", {"command": cmd}))
    assert "curl" in text                                   # the command is shown ...
    assert "SECRETSECRET1234567890" not in text             # ... without the secrets in it
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in text


def test_phone_card_does_not_show_the_content_written_into_a_file():
    text, _ = _phone(_card("Write", {"file_path": str(ROOT / "a.py"),
                                     "content": "PASSWORD = 'hunter2-very-secret'\n"}))
    assert "hunter2-very-secret" not in text


def test_high_risk_card_still_goes_to_the_pc():
    text, buttons = _phone(_card("Bash", {"command": "rm -rf C:/Users"}, "high"))
    assert buttons == []


# ------------------------------------------------------------------ M-4: separate writable folders
def _rw(entries):
    return [Path(p) for p, mode in entries if mode == "RW"]


def _under(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def test_engine_and_test_containers_have_different_temp_folders(isolated):
    proj = isolated / "proj"
    proj.mkdir()
    a, b = sandbox.temp_dir(proj), sandbox.engine_temp_dir(proj)
    assert a != b
    assert not _under(a, b) and not _under(b, a)


def test_the_test_container_cannot_write_the_engine_temp_folder_and_back(isolated):
    proj = isolated / "proj"
    proj.mkdir()
    tests_rw = _rw(sandbox.access_list(proj))
    engine_rw = _rw(sandbox.engine_access_list(proj, config=False))
    assert not any(_under(sandbox.engine_temp_dir(proj), r) for r in tests_rw)
    assert not any(_under(sandbox.temp_dir(proj), r) for r in engine_rw)
    assert sandbox.engine_temp_dir(proj) in engine_rw


def test_nobody_in_a_box_can_write_the_folder_python_loads_sitecustomize_from(isolated):
    proj = isolated / "proj"
    proj.mkdir()
    site = sandbox.site_dir(proj)
    for entries in (sandbox.access_list(proj), sandbox.engine_access_list(proj, config=True)):
        assert not any(_under(site, r) for r in _rw(entries)), "a box can rewrite sitecustomize.py"


def test_the_engine_is_started_with_its_own_temp_folder(isolated, monkeypatch):
    proj = isolated / "proj"
    proj.mkdir()
    monkeypatch.setattr(sandbox, "enabled", lambda: True)
    monkeypatch.setattr(sandbox, "prepare", lambda *a, **k: "S-1-15-2-1")
    monkeypatch.setattr(sandbox, "_allow_engine_folder", lambda *a, **k: None)
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "")
    cmd = sandbox.engine_command([str(isolated / "claude.exe"), "--x"], proj)
    assert "--" in cmd and cmd[3] == str(sandbox.engine_temp_dir(proj))
    assert cmd[3] != str(sandbox.temp_dir(proj))


def test_test_programs_get_the_test_temp_folder_not_the_engines(isolated):
    proj = isolated / "proj"
    proj.mkdir()
    env = sandbox.box_env(proj, {})
    assert env["TEMP"] == str(sandbox.temp_dir(proj)) != str(sandbox.engine_temp_dir(proj))


# ------------------------------------------------------------------ masking
_AK = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
_SK = "s" + "k-" + "abcdefghij1234567890XYZ"
_SL = "x" + "oxb-" + "12345-abcdef"


import pytest  # noqa: E402


@pytest.mark.parametrize("cmd,secret", [
    ("deploy --githubtoken=hunter22", "hunter22"),
    ("export MY_API_KEY=hunter22", "hunter22"),
    ("set DB_PASSWORD: hunter22", "hunter22"),
    ("curl https://bob:hunter22@example.com/x", "hunter22"),
    ("curl -u bob:hunter22 https://example.com", "hunter22"),
    ("curl -H 'Authorization: Basic aHVudGVyMjI6eA==' x", "aHVudGVyMjI6eA"),
    ("run " + _SK, _SK),
    ("aws " + _AK, _AK),
    ("slack " + _SL, _SL),
    ('set password="hunter22 and more"', "and more"),
    ("set secret='hunter22 and more'", "hunter22"),
])
def test_masked_hides_the_secret(cmd, secret):
    assert secret not in telegram._masked(cmd)
