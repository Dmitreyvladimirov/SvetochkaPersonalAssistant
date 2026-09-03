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
    assert r.json() == {"ok": True, "commit": "abcdef123456", "db": {"ok": True, "users": 1}}


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
