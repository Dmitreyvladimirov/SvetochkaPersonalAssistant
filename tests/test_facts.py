"""FR-45: a newer fact closes the old one instead of erasing it; recall by
subject or by value; history on request; isolation."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from tests.fakedb import install


def scope(uid=1):
    return UserScope(user_id=uid, chat_id="111", tz="UTC")


def test_remember_and_recall(monkeypatch):
    fake = install(monkeypatch)
    out = run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Костя", "predicate": "отвечает за", "object": "инфру", "replaces": True})
    assert out.startswith("Remembered fact #")
    assert "Костя — отвечает за — инфру" in run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "инфр", "include_history": False})
    assert "Костя" in run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "костя", "include_history": False})
    assert run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "Маша", "include_history": False}).startswith("No facts")


def test_new_value_closes_the_old_fact_and_keeps_it(monkeypatch):
    fake = install(monkeypatch)
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Света", "predicate": "врач", "object": "Иванова", "replaces": True})
    out = run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "света", "predicate": "Врач", "object": "Петрова", "replaces": True})
    assert "replaces the earlier: 'света — Врач — Иванова'" in out
    current = run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "Света", "include_history": False})
    assert "Петрова" in current and "Иванова" not in current
    history = run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "Света", "include_history": True})
    assert "Иванова" in history and "(до " in history
    assert len(fake.facts) == 2
    # The same fact again is a no-op, not a third row.
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Света", "predicate": "врач", "object": "петрова", "replaces": True})
    assert len(fake.facts) == 2


def test_additive_facts_keep_both(monkeypatch):
    fake = install(monkeypatch)
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Костя", "predicate": "отвечает за", "object": "инфру", "replaces": True})
    out = run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Костя", "predicate": "отвечает за", "object": "бэкапы", "replaces": False})
    assert "replaces" not in out
    recalled = run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "Костя", "include_history": False})
    assert "инфру" in recalled and "бэкапы" in recalled


def test_wildcards_in_topic_are_literal(monkeypatch):
    install(monkeypatch)
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "скидка", "predicate": "размер", "object": "100%", "replaces": True})
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "Костя", "predicate": "отвечает за", "object": "инфру", "replaces": True})
    out = run(REGISTRY, "fact_recall", scope(), ToolContext(), {"topic": "%", "include_history": False})
    assert "100%" in out and "Костя" not in out


def test_memory_show_lists_current_facts(monkeypatch):
    install(monkeypatch)
    run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "ключи", "predicate": "лежат", "object": "в ящике", "replaces": True})
    out = run(REGISTRY, "memory_show", scope(), ToolContext(), {})
    assert "Facts (current):" in out and "ключи — лежат — в ящике" in out


def test_empty_fields_refused(monkeypatch):
    fake = install(monkeypatch)
    assert run(REGISTRY, "fact_remember", scope(), ToolContext(), {"subject": "x", "predicate": "", "object": "y", "replaces": True}).startswith("Error")
    assert fake.facts == {}


def test_facts_never_cross_users(monkeypatch):
    install(monkeypatch)
    run(REGISTRY, "fact_remember", scope(1), ToolContext(), {"subject": "Костя", "predicate": "отвечает за", "object": "инфру", "replaces": True})
    assert run(REGISTRY, "fact_recall", scope(2), ToolContext(), {"topic": "Костя", "include_history": True}).startswith("No facts")
    assert "Facts" not in run(REGISTRY, "memory_show", scope(2), ToolContext(), {})
