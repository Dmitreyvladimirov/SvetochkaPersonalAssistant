"""Tools are deterministic; they get ordinary unit tests."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, UnknownTool, definitions, notes, preferences, run, suggest
from tests.fakedb import install

import pytest


def scope(uid=1):
    return UserScope(user_id=uid, chat_id="111", tz="Asia/Jerusalem")


def test_every_tool_schema_is_strict():
    for d in definitions(REGISTRY):
        assert d["strict"] is True
        assert d["input_schema"]["additionalProperties"] is False
        assert set(d["input_schema"]["required"]) == set(d["input_schema"]["properties"].keys())


def test_unknown_tool_is_an_error_not_an_action(monkeypatch):
    fake = install(monkeypatch)
    with pytest.raises(UnknownTool):
        run(REGISTRY, "note_delete_all", scope(), ToolContext(), {})
    assert fake.notes == {}


def test_note_save_records_source_and_undo_target(monkeypatch):
    fake = install(monkeypatch)
    ctx = ToolContext(inbox_item_id=5)
    out = run(REGISTRY, "note_save", scope(), ctx, {"body": "", "project": "", "url": "https://www.instagram.com/p/abc/"})
    assert "source=instagram" in out
    assert ctx.created_note_ids == [list(fake.notes)[0]]
    assert fake.notes[ctx.created_note_ids[0]]["source_ref"] == "https://www.instagram.com/p/abc/"


def test_note_save_refuses_empty(monkeypatch):
    fake = install(monkeypatch)
    out = run(REGISTRY, "note_save", scope(), ToolContext(), {"body": "  ", "project": "", "url": ""})
    assert out.startswith("Error")
    assert fake.notes == {}


def test_note_search_is_honest_when_empty(monkeypatch):
    install(monkeypatch)
    out = run(REGISTRY, "note_search", scope(), ToolContext(), {"query": "vespera", "source": ""})
    assert "No notes match" in out


def test_source_for_url():
    assert notes.source_for_url("https://www.linkedin.com/posts/x") == "linkedin"
    assert notes.source_for_url("https://t.me/robotsatwork/12") == "telegram"
    assert notes.source_for_url("https://example.com/a") == "web"


def test_preference_roundtrip(monkeypatch):
    install(monkeypatch)
    run(REGISTRY, "preference_set", scope(), ToolContext(), {"key": "persona.brevity", "value": "3"})
    shown = run(REGISTRY, "memory_show", scope(), ToolContext(), {})
    assert "persona.brevity = 3" in shown
    assert "Forgot" in run(REGISTRY, "preference_delete", scope(), ToolContext(), {"key": "persona.brevity"})
    assert "No preferences" in run(REGISTRY, "memory_show", scope(), ToolContext(), {})


def test_suggest_writes_nothing_and_caps_at_three(monkeypatch):
    fake = install(monkeypatch)
    ctx = ToolContext()
    actions = [{"label": f"a{i}", "instruction": f"do {i}"} for i in range(5)]
    run(REGISTRY, "suggest", scope(), ctx, {"actions": actions})
    assert len(ctx.suggestions) == 3
    assert fake.notes == {} and fake.prefs == {}


def test_suggest_honours_proactivity_preference(monkeypatch):
    install(monkeypatch)
    s = UserScope(user_id=1, chat_id="111", tz="UTC", preferences={"persona.proactivity": "0"})
    ctx = ToolContext()
    run(REGISTRY, "suggest", s, ctx, {"actions": [{"label": "x", "instruction": "y"}]})
    assert ctx.suggestions == []
