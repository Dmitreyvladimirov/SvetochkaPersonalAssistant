"""NFR-10, the mandatory cross-user leak test (SPEC.md §10): two users, one saves
"X", the other asks about X and must get nothing — for notes, preferences and
corrections alike."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from tests.fakedb import install


def test_notes_never_cross_users(monkeypatch):
    install(monkeypatch)
    a = UserScope(user_id=1, chat_id="111", tz="UTC")
    b = UserScope(user_id=2, chat_id="222", tz="UTC")
    run(REGISTRY, "note_save", a, ToolContext(), {"body": "Vespera: онбординг через шаблоны", "project": "Vespera", "url": ""})

    assert "No notes match" in run(REGISTRY, "note_search", b, ToolContext(), {"query": "Vespera", "source": ""})
    assert "No notes yet" in run(REGISTRY, "note_recent", b, ToolContext(), {"project": "", "limit": 10})
    assert "1 note(s)" in run(REGISTRY, "note_search", a, ToolContext(), {"query": "Vespera", "source": ""})


def test_preferences_and_memory_never_cross_users(monkeypatch):
    fake = install(monkeypatch)
    a = UserScope(user_id=1, chat_id="111", tz="UTC")
    b = UserScope(user_id=2, chat_id="222", tz="UTC")
    run(REGISTRY, "preference_set", a, ToolContext(), {"key": "persona.brevity", "value": "3"})
    fake.add_correction(1, None, "saved note #1", "should have been a list")

    assert "No preferences" in run(REGISTRY, "memory_show", b, ToolContext(), {})
    assert "corrections" not in run(REGISTRY, "memory_show", b, ToolContext(), {}).lower()
    assert "persona.brevity = 3" in run(REGISTRY, "memory_show", a, ToolContext(), {})


def test_undo_cannot_touch_another_users_note(monkeypatch):
    fake = install(monkeypatch)
    from sveta.core import db
    nid = db.create_note(1, "mine")
    assert db.soft_delete_note(2, nid) is False
    assert fake.notes[nid]["deleted_at"] is None
