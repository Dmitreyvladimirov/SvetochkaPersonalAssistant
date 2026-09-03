"""§6.5: the secret in the path, the secret in the header, fail closed."""
from fastapi.testclient import TestClient

from sveta.core import app as app_module, bot, config, db


def _client(monkeypatch):
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "seed_users", lambda chat_ids, tz: 0)
    monkeypatch.setattr(app_module, "_register_webhook", lambda: None)
    started = []
    monkeypatch.setattr(bot, "start", lambda update: started.append(update))
    return TestClient(app_module.app), started


def test_unset_secret_fails_closed(monkeypatch):
    client, started = _client(monkeypatch)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    with client:
        r = client.post("/tg/anything", json={"update_id": 1})
    assert r.status_code == 503 and started == []


def test_wrong_path_secret_is_403(monkeypatch):
    client, started = _client(monkeypatch)
    with client:
        r = client.post("/tg/wrong", json={"update_id": 1},
                        headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET})
    assert r.status_code == 403 and started == []


def test_missing_header_is_403_even_with_right_path(monkeypatch):
    client, started = _client(monkeypatch)
    with client:
        r = client.post(f"/tg/{config.WEBHOOK_SECRET}", json={"update_id": 1})
    assert r.status_code == 403 and started == []


def test_both_secrets_right_is_200_and_dispatches_once(monkeypatch):
    client, started = _client(monkeypatch)
    with client:
        r = client.post(f"/tg/{config.WEBHOOK_SECRET}", json={"update_id": 1, "message": {}},
                        headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert started == [{"update_id": 1, "message": {}}]


def test_malformed_body_is_400(monkeypatch):
    client, started = _client(monkeypatch)
    with client:
        r = client.post(f"/tg/{config.WEBHOOK_SECRET}", content=b"not json",
                        headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET,
                                 "Content-Type": "application/json"})
    assert r.status_code == 400 and started == []
