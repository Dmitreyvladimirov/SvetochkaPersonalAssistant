"""/health is what Railway's healthcheck reads: 200 only when the database answers."""
from fastapi.testclient import TestClient

from sveta.core import app as app_module, db


def _client(monkeypatch, ping):
    monkeypatch.setattr(db, "ping", ping)
    # Startup would open a real connection; stage 0 tests never touch Postgres.
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(db, "seed_users", lambda chat_ids, tz: 0)
    return TestClient(app_module.app)


def test_health_is_200_with_commit_when_db_answers(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")
    with _client(monkeypatch, lambda: {"ok": True, "users": 1}) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["commit"] == "abcdef123456"
    assert body["db"] == {"ok": True, "users": 1} and body["tick_alive"] is False


def test_health_is_503_when_db_is_down(monkeypatch):
    def dead():
        raise ConnectionError("no route to host")
    with _client(monkeypatch, dead) as client:
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_startup_validates_before_touching_the_database(monkeypatch):
    # A missing variable must surface as "Missing required env vars", not as a
    # psycopg2 traceback from init_db.
    import pytest
    from sveta.core import config
    monkeypatch.setattr(config, "validate_secrets",
                        lambda: (_ for _ in ()).throw(EnvironmentError("Missing required env vars: X")))
    touched = []
    monkeypatch.setattr(db, "init_db", lambda: touched.append("init_db"))
    with pytest.raises(EnvironmentError, match="Missing required env vars"):
        with TestClient(app_module.app):
            pass
    assert touched == []


# --- Stage 3: the Google OAuth callback ----------------------------------------

def test_oauth_callback_rejects_a_bad_state(monkeypatch):
    from tests.fakedb import install
    install(monkeypatch)
    with _client(monkeypatch, lambda: {"ok": True, "users": 1}) as client:
        r = client.get("/oauth/google/callback", params={"state": "1.deadbeef.cafe", "code": "x"})
    assert r.status_code == 400


def test_oauth_callback_exchanges_and_tells_the_chat(monkeypatch):
    from sveta.core import google, telegram
    from tests.fakedb import install
    fake = install(monkeypatch)
    fake.add_user("111")
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, chat_id, reply_markup=None: sent.append((str(chat_id), text)) or 1)
    monkeypatch.setattr(google, "exchange_code", lambda user_id, code: "dima@example.com")
    with _client(monkeypatch, lambda: {"ok": True, "users": 1}) as client:
        state = google.state_for(1)
        r = client.get("/oauth/google/callback", params={"state": state, "code": "c0de"})
        assert r.status_code == 200 and "Готово" in r.text
        r_replay = client.get("/oauth/google/callback", params={"state": state, "code": "c0de"})
        assert r_replay.status_code == 400                       # a state is single-use
        r2 = client.get("/oauth/google/callback", params={"state": google.state_for(1), "error": "access_denied"})
        assert r2.status_code == 200
    assert sent[0] == ("111", "Google подключён: dima@example.com. Календарь и почта теперь доступны — "
                              "спроси «что у меня завтра» или «найди письмо про …».")
    assert "не дал доступ" in sent[1][1]
