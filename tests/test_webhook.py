"""§6.5: a fixed path, the secret only in the header, fail closed.

The secret must never appear in a URL: every access log prints paths. This file
guards that with a test on the route itself, not only on the checks."""
from fastapi.testclient import TestClient

from sveta.core import app as app_module, bot, config, db

HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _client(monkeypatch):
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "seed_users", lambda chat_ids, tz: 0)
    monkeypatch.setattr(app_module, "_register_webhook", lambda: None)
    return TestClient(app_module.app)


def test_route_path_carries_no_secret(monkeypatch):
    assert app_module.WEBHOOK_PATH == "/tg/webhook"
    assert config.WEBHOOK_SECRET not in app_module.WEBHOOK_PATH
    routes = {r.path for r in app_module.app.routes}
    assert app_module.WEBHOOK_PATH in routes
    assert not any("{" in p and p.startswith("/tg") for p in routes), "no path parameter on the webhook"


def test_unset_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    with _client(monkeypatch) as client:
        r = client.post(app_module.WEBHOOK_PATH, json={"update_id": 1},
                        headers={HEADER: "anything"})
    assert r.status_code == 503


def test_missing_header_is_403(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.post(app_module.WEBHOOK_PATH, json={"update_id": 1})
    assert r.status_code == 403


def test_wrong_header_is_403(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.post(app_module.WEBHOOK_PATH, json={"update_id": 1},
                        headers={HEADER: "wrong"})
    assert r.status_code == 403


def test_secret_in_path_alone_is_still_403(monkeypatch):
    # The old contract. Knowing the secret must not help without the header, and
    # the old URL must not exist at all.
    with _client(monkeypatch) as client:
        r = client.post(f"/tg/{config.WEBHOOK_SECRET}", json={"update_id": 1})
    assert r.status_code in (403, 404, 405)


def test_right_header_is_200_and_dispatches_once(monkeypatch):
    started = []
    monkeypatch.setattr(bot, "start", lambda update: started.append(update))
    with _client(monkeypatch) as client:
        r = client.post(app_module.WEBHOOK_PATH, json={"update_id": 1, "message": {}},
                        headers={HEADER: config.WEBHOOK_SECRET})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert started == [{"update_id": 1, "message": {}}]


def test_malformed_body_is_400_and_not_dispatched(monkeypatch):
    started = []
    monkeypatch.setattr(bot, "start", lambda update: started.append(update))
    with _client(monkeypatch) as client:
        r = client.post(app_module.WEBHOOK_PATH, content=b"not json",
                        headers={HEADER: config.WEBHOOK_SECRET,
                                 "Content-Type": "application/json"})
    assert r.status_code == 400
    assert started == []


def test_registration_url_has_no_secret(monkeypatch):
    seen = {}
    from sveta.core import telegram
    monkeypatch.setattr(telegram, "set_webhook", lambda url, secret: seen.update(url=url, secret=secret) or True)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "example.up.railway.app")
    app_module._register_webhook()
    assert seen["url"] == "https://example.up.railway.app/tg/webhook"
    assert seen["secret"] == config.WEBHOOK_SECRET
    assert config.WEBHOOK_SECRET not in seen["url"]
