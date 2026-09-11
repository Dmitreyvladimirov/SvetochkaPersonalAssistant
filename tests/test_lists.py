"""Lists: free-form lines, created on first use, checkboxes, moves, isolation."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from tests.fakedb import install


def scope(uid=1):
    return UserScope(user_id=uid, chat_id="111", tz="Asia/Jerusalem")


def test_add_creates_the_list_and_marks_it_for_rendering(monkeypatch):
    fake = install(monkeypatch)
    ctx = ToolContext()
    out = run(REGISTRY, "list_add", scope(), ctx, {"list_name": "Покупки", "items": ["молоко", " батарейки ", ""]})
    assert out.startswith("Added 2 line(s) to 'Покупки'")
    assert "1. ☐ молоко" in out and "2. ☐ батарейки" in out
    assert len(ctx.created_list_item_ids) == 2 and ctx.render_list_id == list(fake.lists)[0]


def test_names_match_case_insensitively(monkeypatch):
    fake = install(monkeypatch)
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "покупки", "items": ["а"]})
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "Покупки ", "items": ["б"]})
    assert len(fake.lists) == 1 and len(fake.list_items) == 2


def test_show_overview_and_one_list_and_text_form(monkeypatch):
    install(monkeypatch)
    assert run(REGISTRY, "list_show", scope(), ToolContext(), {"list_name": "", "as_text": False}) == "No lists yet."
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "Сегодня", "items": ["позвонить маме", "оплатить счёт"]})
    ctx = ToolContext()
    out = run(REGISTRY, "list_show", scope(), ctx, {"list_name": "сегодня", "as_text": False})
    assert "checkboxes" in out and ctx.render_list_id is not None
    ctx = ToolContext()
    out = run(REGISTRY, "list_show", scope(), ctx, {"list_name": "сегодня", "as_text": True})
    assert out.startswith("Plain text") and ctx.render_list_id is None
    overview = run(REGISTRY, "list_show", scope(), ToolContext(), {"list_name": "", "as_text": False})
    assert "- Сегодня: 2 open of 2" in overview
    assert run(REGISTRY, "list_show", scope(), ToolContext(), {"list_name": "Книги", "as_text": False}).startswith("No list named")


def test_check_by_number_and_by_text(monkeypatch):
    fake = install(monkeypatch)
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "Покупки", "items": ["молоко", "батарейки", "хлеб"]})
    assert run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "покупки", "item": "2", "checked": True}) == "Marked 'батарейки' as checked in 'Покупки'."
    assert run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "покупки", "item": "Молоко", "checked": True}).startswith("Marked 'молоко'")
    assert run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "покупки", "item": "хле", "checked": True}).startswith("Marked 'хлеб'")
    assert run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "покупки", "item": "сыр", "checked": True}).startswith("No line matching")
    assert all(i["checked_at"] for i in fake.list_items.values())
    run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "покупки", "item": "1", "checked": False})
    assert [i["checked_at"] is None for i in fake.list_items.values()] == [True, False, False]


def test_move_keeps_history_and_creates_the_target(monkeypatch):
    fake = install(monkeypatch)
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "Покупки", "items": ["батарейки"]})
    ctx = ToolContext()
    out = run(REGISTRY, "list_move", scope(), ctx, {"item": "батарейки", "from_list": "покупки", "to_list": "Большие покупки"})
    assert out == "Moved 'батарейки' from 'Покупки' to 'Большие покупки'."
    item = list(fake.list_items.values())[0]
    assert item["moved_from"] == list(fake.lists)[0] and item["list_id"] == ctx.render_list_id
    assert run(REGISTRY, "list_move", scope(), ToolContext(), {"item": "батарейки", "from_list": "покупки", "to_list": "x"}).startswith("No line")


def test_lists_never_cross_users(monkeypatch):
    install(monkeypatch)
    run(REGISTRY, "list_add", scope(1), ToolContext(), {"list_name": "Покупки", "items": ["молоко"]})
    assert run(REGISTRY, "list_show", scope(2), ToolContext(), {"list_name": "", "as_text": False}) == "No lists yet."
    assert run(REGISTRY, "list_show", scope(2), ToolContext(), {"list_name": "Покупки", "as_text": False}).startswith("No list named")
    assert run(REGISTRY, "list_check", scope(2), ToolContext(), {"list_name": "Покупки", "item": "1", "checked": True}).startswith("No list named")


def test_unchecked_helper_for_the_brief(monkeypatch):
    install(monkeypatch)
    from sveta.core import db
    run(REGISTRY, "list_add", scope(), ToolContext(), {"list_name": "Сегодня", "items": ["а", "б"]})
    run(REGISTRY, "list_check", scope(), ToolContext(), {"list_name": "Сегодня", "item": "а", "checked": True})
    assert [i["text"] for i in db.unchecked_list_items(1, "сегодня")] == ["б"]
    assert db.unchecked_list_items(2, "сегодня") == []
