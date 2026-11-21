"""L59: opening a project with a long chat was slow (2 s on the owner's PC): the whole history was sent and
drawn, and the cost estimate read the settings file once per usage line."""
import json
import time

from app import events as events_mod, iterations, models, projects, settings
import test_server

srv = test_server.srv


def _long_chat(slug, n=1000, pending_at=None):
    f = projects.history_file(slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as h:
        for i in range(n):
            ev = {"type": "message", "role": "assistant", "text": f"message {i}", "slug": slug, "seq": 1000 + i, "ts": 1.0 + i}
            if i == pending_at:
                ev = {"type": "question", "id": "q-old", "status": "pending", "slug": slug, "seq": 1000 + i, "ts": 1.0 + i,
                      "title": "an old question"}
            h.write(json.dumps(ev) + "\n")


def test_prices_are_read_once_per_estimate(isolated, monkeypatch):
    settings.save({"setup_complete": True})
    slug = projects.create("Invoices")["slug"]
    log = projects.project_dir(slug) / ".aiteam" / "usage.jsonl"
    log.parent.mkdir(exist_ok=True)
    rows = [{"ts": time.time(), "model": "claude-sonnet-4-5", "input_tokens": 1000, "output_tokens": 100,
             "slug": slug, "agent": "Fred"} for _ in range(400)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    models.forget_prices()
    calls = []
    real = settings.load
    monkeypatch.setattr(settings, "load", lambda: calls.append(1) or real())
    iterations.cost_estimate(slug)
    assert len(calls) < 20, len(calls)        # was one read per usage line
    settings.save({"prices": {"claude-sonnet-4-5": {"input": 1, "output": 1}}})
    assert models.prices()["claude-sonnet-4-5"] == {"input": 1, "output": 1}   # a change is seen at once


def test_history_is_parsed_once_per_version(isolated, monkeypatch):
    settings.save({"setup_complete": True})
    slug = projects.create("Chatty")["slug"]
    _long_chat(slug, 50)
    hub = events_mod.EventHub(projects.history_file)
    parsed = []
    real = events_mod.EventHub._parse
    monkeypatch.setattr(events_mod.EventHub, "_parse", staticmethod(lambda f: parsed.append(1) or real(f)))
    a = hub.history(slug)
    a[0]["status"] = "changed by a caller"
    b = hub.history(slug)
    assert len(parsed) == 1 and "status" not in b[0]          # cached, and callers get their own copy
    hub.publish(slug, {"type": "message", "role": "user", "text": "new"})
    assert hub.history(slug)[-1]["text"] == "new" and len(parsed) == 2


def test_only_the_latest_part_is_sent_and_earlier_on_request(srv):
    settings.save({"setup_complete": True})
    slug = projects.create("Long chat")["slug"]
    _long_chat(slug, 1000, pending_at=10)
    status, view = srv.call("GET", f"/api/projects/{slug}")
    texts = [e.get("text") for e in view["history"]]
    assert status == 200 and view["history_from"] == 1000 - 300
    assert texts[-1] == "message 999" and len(view["history"]) <= 301
    assert not [e for e in view["history"] if e.get("id") == "q-old"]   # expired after a restart: not pulled forward
    status, more = srv.call("GET", f"/api/projects/{slug}/history?before={view['history_from']}")
    assert status == 200 and more["from"] == view["history_from"] - 300 and len(more["items"]) == 300
    assert more["items"][-1]["seq"] < view["history"][1]["seq"]
    status, rest = srv.call("GET", f"/api/projects/{slug}/history?before={more['from']}&limit=5000")
    assert rest["from"] == 0 and len(rest["items"]) == more["from"]
    assert srv.call("GET", f"/api/projects/{slug}/history?before=x")[0] == 400
    assert srv.call("GET", "/api/projects/nope/history?before=10")[0] == 404


def test_a_card_still_waiting_is_always_sent(srv, monkeypatch):
    settings.save({"setup_complete": True})
    slug = projects.create("Waiting")["slug"]
    _long_chat(slug, 800, pending_at=5)
    monkeypatch.setattr(srv.app.engine, "pending_for", lambda s: {"q-old"} if s == slug else set())
    view = srv.call("GET", f"/api/projects/{slug}")[1]
    first = view["history"][0]
    assert first["id"] == "q-old" and first["status"] == "pending"       # the user can still answer it
    assert view["history_from"] == 500
