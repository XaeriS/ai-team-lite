import time

from app import auth, paths, projects, reset, secrets_store, settings


def test_cli_env_modes(monkeypatch):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    assert auth.cli_env("api_key")["ANTHROPIC_API_KEY"] == "sk-ant-x"
    env = auth.cli_env("subscription")
    assert env["ANTHROPIC_API_KEY"] == "" and env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(paths.data_dir()))


def test_ready_per_mode():
    assert not auth.ready()
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    assert auth.ready()  # no mode chosen yet -> API key counts
    settings.save({"auth_mode": "subscription", "subscription_signed_in": False})
    assert not auth.ready()
    settings.save({"subscription_signed_in": True})
    assert auth.ready()


def test_status_parses_cli(monkeypatch):
    class R:
        returncode = 0
        stderr = ""

        def __init__(self, out):
            self.stdout = out
    monkeypatch.setattr(auth, "_run", lambda args, timeout=60: R('{"loggedIn": true, "authMethod": "claude.ai"}'))
    assert auth.subscription_status()["signed_in"] is True
    monkeypatch.setattr(auth, "_run", lambda args, timeout=60: R('{"loggedIn": false}'))
    assert auth.subscription_status()["signed_in"] is False
    monkeypatch.setattr(auth, "_run", lambda args, timeout=60: R('{"loggedIn": true, "authMethod": "api_key"}'))
    assert auth.subscription_status()["signed_in"] is False, "an API key is not a subscription sign-in"


def test_engine_uses_subscription_env(isolated):
    from app import engine as engine_mod
    from app.events import EventHub
    from fakeclaude import FakeClient, say

    settings.save({"auth_mode": "subscription", "subscription_signed_in": True, "setup_complete": True})
    secrets_store.set_secret("anthropic_api_key", "sk-ant-should-not-be-used")
    p = projects.create("Sub")

    async def script(i, prompt, c):
        yield say("hi")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "hello")
    for _ in range(100):
        time.sleep(0.05)
        if not eng.is_busy(p["slug"]) and FakeClient.instances and FakeClient.instances[-1].prompts:
            break
    time.sleep(0.2)
    eng.shutdown()
    env = FakeClient.instances[-1].options.env
    assert env["ANTHROPIC_API_KEY"] == ""
    assert any(e["type"] == "message" and e["role"] == "fred" for e in hub.history(p["slug"]))


def test_reset_erases_app_data_but_not_projects(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    secrets_store.set_secret("github_token", "ghp_x")
    settings.save({"setup_complete": True, "user_name": "Fred"})
    p = projects.create("Keep me")
    proj_dir = projects.project_dir(p["slug"])
    (paths.data_dir() / "claude").mkdir(exist_ok=True)
    (paths.data_dir() / "claude" / ".credentials.json").write_text("{}")
    done = reset.reset()
    assert not secrets_store.has("anthropic_api_key") and not secrets_store.has("github_token")
    assert not (isolated / "data").exists()
    assert proj_dir.exists() and (proj_dir / ".aiteam" / "project.json").exists()
    assert any("erased" in d for d in done)
    assert settings.load()["setup_complete"] is False  # back to first-run state
