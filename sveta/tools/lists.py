"""Named free-form lists (FR-47…FR-52). A line is any text; "Сегодня" and
"Покупки" are ordinary lists that appear on first use, like any other name.

Rendering is the bot's job: a tool sets ctx.render_list_id and the reply carries
one checkbox button per line (FR-49). `render()` here builds the text both the
tool result and the message use, so the model and the user see the same list."""
from sveta.core import db
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

MAX_BUTTON_LINES = 30


def render(list_row: dict, items: list[dict]) -> str:
    if not items:
        return f"{list_row['name']}: пусто."
    lines = [f"{list_row['name']}:"]
    for n, item in enumerate(items, 1):
        box = "☑" if item["checked_at"] else "☐"
        lines.append(f"{n}. {box} {item['text']}")
    return "\n".join(lines)


def _find_item(items: list[dict], item: str) -> dict | None:
    """By 1-based number, then by exact text, then by unique substring."""
    item = (item or "").strip()
    if not item:
        return None
    if item.isdigit() and 1 <= int(item) <= len(items):
        return items[int(item) - 1]
    lower = item.lower()
    exact = [i for i in items if i["text"].lower() == lower]
    if exact:
        return exact[0]
    partial = [i for i in items if lower in i["text"].lower()]
    return partial[0] if len(partial) == 1 else None


def _add(scope: UserScope, ctx: ToolContext, list_name: str, items: list) -> str:
    name = (list_name or "").strip()
    texts = [str(t).strip() for t in (items or []) if str(t).strip()]
    if not name:
        return "Error: list_name is empty."
    if not texts:
        return "Error: nothing to add — items is empty."
    row = db.get_or_create_list(scope.user_id, name)
    ids = db.add_list_items(scope.user_id, row["id"], texts)
    ctx.created_list_item_ids.extend(ids)
    ctx.render_list_id = row["id"]
    all_items = db.list_items(scope.user_id, row["id"])
    return (f"Added {len(ids)} line(s) to '{row['name']}'. The list will be shown with "
            f"checkboxes under your reply; do not repeat it in full.\n" + render(row, all_items))


LIST_ADD = Tool(
    name="list_add",
    description=("Add one or more lines to a named list; the list is created on first use. "
                 "'добавь в покупки молоко и батарейки' → list_name='Покупки', items=['молоко', "
                 "'батарейки']. 'сегодня ещё позвонить маме' → list_name='Сегодня'. Split "
                 "'и'/commas into separate lines."),
    input_schema={
        "type": "object",
        "properties": {
            "list_name": {"type": "string", "description": "List name as the user calls it (Покупки, Сегодня, Книги…)."},
            "items": {"type": "array", "items": {"type": "string"}, "description": "Lines to add, one per item."},
        },
    },
    fn=_add,
)


def _show(scope: UserScope, ctx: ToolContext, list_name: str, as_text: bool) -> str:
    name = (list_name or "").strip()
    if not name:
        lists = db.list_lists(scope.user_id)
        if not lists:
            return "No lists yet."
        return "Lists:\n" + "\n".join(f"- {l['name']}: {l['unchecked']} open of {l['total']}" for l in lists)
    row = db.find_list(scope.user_id, name)
    if not row:
        return f"No list named '{name}'. Say so; offer to create it by adding something."
    items = db.list_items(scope.user_id, row["id"])
    if as_text:
        return "Plain text, copyable:\n" + render(row, items)
    ctx.render_list_id = row["id"]
    return ("The list will be shown with checkboxes under your reply; do not repeat it in full.\n"
            + render(row, items))


LIST_SHOW = Tool(
    name="list_show",
    description=("Show a list with checkboxes, or all lists when list_name is empty. "
                 "'что мне сегодня делать' → 'Сегодня'; 'что в покупках' → 'Покупки'. "
                 "as_text=true returns plain text for copying ('покажи покупки текстом')."),
    input_schema={
        "type": "object",
        "properties": {
            "list_name": {"type": "string", "description": "List name, or empty string for an overview of all lists."},
            "as_text": {"type": "boolean", "description": "true for a plain-text copyable version, false for checkboxes."},
        },
    },
    fn=_show,
)


def _check(scope: UserScope, ctx: ToolContext, list_name: str, item: str, checked: bool) -> str:
    row = db.find_list(scope.user_id, (list_name or "").strip())
    if not row:
        return f"No list named '{list_name}'."
    items = db.list_items(scope.user_id, row["id"])
    target = _find_item(items, item)
    if not target:
        return f"No line matching '{item}' in '{row['name']}'. Lines: " + "; ".join(i["text"] for i in items)
    db.set_list_item_checked(scope.user_id, target["id"], bool(checked))
    ctx.render_list_id = row["id"]
    state = "checked" if checked else "unchecked"
    return f"Marked '{target['text']}' as {state} in '{row['name']}'."


LIST_CHECK = Tool(
    name="list_check",
    description=("Check or uncheck a line: 'вычеркни молоко', 'купил батарейки' → checked=true; "
                 "'верни молоко' → checked=false. item is the line number or its text."),
    input_schema={
        "type": "object",
        "properties": {
            "list_name": {"type": "string", "description": "Which list."},
            "item": {"type": "string", "description": "Line number (1-based) or the line's text."},
            "checked": {"type": "boolean", "description": "true to check off, false to reopen."},
        },
    },
    fn=_check,
)


def _move(scope: UserScope, ctx: ToolContext, item: str, from_list: str, to_list: str) -> str:
    src = db.find_list(scope.user_id, (from_list or "").strip())
    if not src:
        return f"No list named '{from_list}'."
    items = db.list_items(scope.user_id, src["id"])
    target = _find_item(items, item)
    if not target:
        return f"No line matching '{item}' in '{src['name']}'."
    dst = db.get_or_create_list(scope.user_id, (to_list or "").strip() or "Разное")
    if dst["id"] == src["id"]:
        return "Error: from_list and to_list are the same list."
    db.move_list_item(scope.user_id, target["id"], dst["id"])
    ctx.render_list_id = dst["id"]
    return f"Moved '{target['text']}' from '{src['name']}' to '{dst['name']}'."


LIST_MOVE = Tool(
    name="list_move",
    description="Move a line between lists: 'перенеси батарейки в большие покупки'. The target list is created if missing; history is kept.",
    input_schema={
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "Line number or text."},
            "from_list": {"type": "string", "description": "Source list name."},
            "to_list": {"type": "string", "description": "Target list name."},
        },
    },
    fn=_move,
)
