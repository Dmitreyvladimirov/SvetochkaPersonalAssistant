"""One Telegram update, start to finish (SPEC.md §4.1).

Runs in a background thread off the webhook: Telegram redelivers an update it has
not seen acknowledged within seconds, and an agent turn takes several of them.

`handle_update` never raises. An exception in a daemon thread is invisible from the
phone — the user sees "приняла…" and then nothing, which reads as a hung assistant.
Every path ends in a message (FR-7).
"""
import logging
import re
import threading

from sveta.core import agent, config, db, llm, telegram
from sveta.core.scope import UserScope
from sveta.tools import notes as notes_tool

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

HELP = (
    "Я Светочка. Пиши или наговаривай — разложу сама.\n\n"
    "Уже умею:\n"
    "• записать мысль, идею, ссылку — и найти их потом («что я писал про …»)\n"
    "• запомнить, как с тобой общаться («будь короче», «хватит здороваться»)\n"
    "• предложить следующий шаг кнопкой\n\n"
    "Пока не умею (в работе): голосовые, напоминания, списки, календарь, почта, "
    "утренний бриф.\n\n"
    "/ping — жива ли · /stats — расходы за сегодня · /memory — что помню о тебе"
)


def start(update: dict) -> None:
    """The webhook route's only job."""
    threading.Thread(target=handle_update, args=(update,), daemon=True).start()


def handle_update(update: dict) -> None:
    try:
        if "callback_query" in update:
            _handle_callback(update.get("update_id"), update["callback_query"])
        elif "message" in update:
            _handle_message(update.get("update_id"), update["message"])
        elif "edited_message" in update:
            _handle_message(update.get("update_id"), update["edited_message"])
    except Exception:  # noqa: BLE001
        logger.exception("bot: update handling failed")


def _scope_for(chat_id) -> UserScope | None:
    """FR-2: no users row → no reply at all. A refusal would confirm the bot exists."""
    user = db.get_user_by_chat(chat_id)
    if not user:
        logger.warning("bot: message from unregistered chat %s — ignored", chat_id)
        return None
    return UserScope(user_id=user["id"], chat_id=str(chat_id), tz=user.get("tz") or config.TZ,
                     preferences=db.list_preferences(user["id"]))


def _handle_message(update_id, message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    scope = _scope_for(chat_id)
    if scope is None:
        return

    voice = message.get("voice") or message.get("audio")
    text = (message.get("text") or message.get("caption") or "").strip()
    kind = "voice" if voice else "text"

    # Idempotency before anything paid and before any reply (FR-3, FR-4).
    try:
        item_id = db.claim_update(update_id, scope.user_id, message_id=message.get("message_id"),
                                  kind=kind, raw_text=text or None,
                                  file_id=(voice or {}).get("file_id"),
                                  duration_sec=(voice or {}).get("duration"))
    except Exception:  # noqa: BLE001
        logger.exception("bot: could not record the update — refusing to process it")
        telegram.send_message("База не ответила, я это сообщение не сохранила. Повтори, пожалуйста.", chat_id)
        return
    if item_id is None:
        logger.info("bot: update %s already handled — skipping", update_id)
        return

    if voice:
        db.mark_item(item_id, status="new", reply_text=None)
        telegram.send_message("Голосовые ещё не расшифровываю — это следующий этап. "
                              "Сохранила, разберу, когда научусь.", chat_id)
        return

    if not text:
        db.mark_item(item_id, status="done")
        telegram.send_message("Вижу сообщение, но в нём нет текста.", chat_id)
        return

    # --- Cheap path (FR-6): no model for commands and bare URLs. ---
    if text.startswith("/"):
        reply = _command(scope, text)
        db.mark_item(item_id, status="done", reply_text=reply)
        telegram.send_message(reply, chat_id)
        return

    if _URL_RE.match(text):
        note_id = db.create_note(scope.user_id, text, source=notes_tool.source_for_url(text),
                                 source_ref=text, inbox_item_id=item_id)
        reply = "Сохранила ссылку. Что внутри — пока не читаю, это следующий этап."
        db.mark_item(item_id, status="done", reply_text=reply)
        telegram.send_message(reply, chat_id, _keyboard(item_id, [note_id], []))
        return

    _run_agent(scope, item_id, text, chat_id)


def _run_agent(scope: UserScope, item_id: int, text: str, chat_id) -> None:
    progress_id = telegram.send_message("Приняла, разбираю…", chat_id)
    try:
        history = db.recent_exchanges(scope.user_id)
    except Exception:  # noqa: BLE001
        logger.exception("bot: history load failed — continuing without it")
        history = []

    try:
        result = agent.run(scope, text, inbox_item_id=item_id, history=history)
    except llm.BudgetExceeded as e:
        logger.warning("bot: budget exceeded for user %s — %s", scope.user_id, e)
        reply = ("Упёрлась в дневной лимит на модель. Сообщение сохранила — разберу, "
                 "когда лимит обновится или ты его поднимешь.")
        db.mark_item(item_id, status="new", error=str(e), reply_text=None)
        _reply(chat_id, progress_id, reply)
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("bot: agent failed for item %s", item_id)
        db.mark_item(item_id, status="failed", error=str(e)[:500])
        _reply(chat_id, progress_id, "Не смогла разобрать — модель не ответила. Сообщение "
                                     "сохранила, попробую позже.")
        return

    suggestions = result.ctx.suggestions
    db.mark_item(item_id, status="done", reply_text=result.reply,
                 suggestions=suggestions if suggestions else None)
    _reply(chat_id, progress_id, result.reply,
           _keyboard(item_id, result.ctx.created_note_ids, suggestions))


def _command(scope: UserScope, text: str) -> str:
    cmd = text.split()[0].lower()
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/ping":
        return "Жива."
    if cmd == "/stats":
        try:
            return f"Потрачено сегодня: ${db.spend_today(scope.user_id):.3f} из ${config.DAILY_USD_LIMIT:.2f}"
        except Exception:  # noqa: BLE001
            logger.exception("bot: /stats failed")
            return "Счётчик расходов недоступен."
    if cmd == "/memory":
        from sveta.tools import preferences as prefs_tool
        from sveta.core.scope import ToolContext
        return prefs_tool.MEMORY_SHOW.fn(scope, ToolContext())
    return "Такой команды нет. /help — что умею."


def _keyboard(item_id: int, note_ids: list[int], suggestions: list[dict]) -> dict | None:
    rows = []
    if note_ids:
        rows.append([{"text": "✖︎ Не туда", "callback_data": f"undo:{note_ids[-1]}"}])
    for n, s in enumerate(suggestions[:3]):
        rows.append([{"text": s["label"][:40], "callback_data": f"sg:{item_id}:{n}"}])
    return {"inline_keyboard": rows} if rows else None


def _handle_callback(update_id, callback: dict) -> None:
    chat_id = callback.get("message", {}).get("chat", {}).get("id")
    scope = _scope_for(chat_id)
    if scope is None:
        return
    telegram.answer_callback(callback.get("id", ""))

    data = callback.get("data") or ""
    action, _, rest = data.partition(":")

    if action == "undo" and rest.isdigit():
        note_id = int(rest)
        if db.soft_delete_note(scope.user_id, note_id):
            db.add_correction(scope.user_id, None, f"saved note #{note_id}", None)
            telegram.send_message("Убрала. Скажи, куда это на самом деле — запомню.", chat_id)
        else:
            telegram.send_message("Эту запись уже убрала раньше.", chat_id)
        return

    if action == "sg":
        item_part, _, n_part = rest.partition(":")
        if not (item_part.isdigit() and n_part.isdigit()):
            return
        item = db.get_item(scope.user_id, int(item_part))
        suggestions = (item or {}).get("suggestions") or []
        n = int(n_part)
        if n >= len(suggestions):
            telegram.send_message("Эта кнопка уже отработала или устарела.", chat_id)
            return
        instruction = suggestions[n]["instruction"]
        # A tap is a new incoming item: it gets its own inbox row (and its own
        # idempotency via the callback's update_id).
        new_item = db.claim_update(update_id, scope.user_id, kind="suggestion",
                                   raw_text=instruction)
        if new_item is None:
            return
        _run_agent(scope, new_item, instruction, chat_id)


def _reply(chat_id, progress_id: int | None, text: str, markup: dict | None = None) -> None:
    """Rewrite the placeholder, or send fresh if there was none."""
    if progress_id:
        telegram.edit_message(progress_id, text, chat_id, markup)
    else:
        telegram.send_message(text, chat_id, markup)
