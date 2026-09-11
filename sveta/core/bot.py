"""One Telegram update, start to finish (SPEC.md §4.1).

Runs in a background thread off the webhook: Telegram redelivers an update it has
not seen acknowledged within seconds, and an agent turn takes several of them.

`handle_update` never raises. An exception in a daemon thread is invisible from the
phone — the user sees "приняла…" and then nothing, which reads as a hung assistant.
Every path ends in a message (FR-7).

Stage 2 adds three things here: the voice branch (transcribe, then the same text
pipeline), list checkbox keyboards with in-place toggling, and reminder buttons.
"""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sveta.core import agent, config, db, llm, telegram, timeparse, transcribe
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import links as links_tool
from sveta.tools import lists as lists_tool

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

HELP = (
    "Я Светочка. Пиши или наговаривай — разложу сама.\n\n"
    "Уже умею:\n"
    "• записать мысль, идею, ссылку — и найти их потом («что я писал про …»)\n"
    "• голосовые: расшифрую и разберу как текст (до 10 минут)\n"
    "• напомнить: «напомни в четверг в 11 позвонить в банк», «через 20 минут»\n"
    "• списки с галочками: «добавь в покупки молоко и батарейки», «что мне сегодня делать»\n"
    "• запомнить, как с тобой общаться («будь короче», «хватит здороваться»)\n"
    "• предложить следующий шаг кнопкой\n\n"
    "Пока не умею (в работе): календарь, почта, утренний бриф.\n\n"
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


# --- Messages ----------------------------------------------------------------

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
        _handle_voice(scope, item_id, voice, chat_id)
        return

    if not text:
        db.mark_item(item_id, status="done")
        telegram.send_message("Вижу сообщение, но в нём нет текста.", chat_id)
        return

    _process_text(scope, item_id, text, chat_id)


def _handle_voice(scope: UserScope, item_id: int, voice: dict, chat_id) -> None:
    """FR-16…18. The duration check comes first: over the limit means no getFile,
    no download, no Whisper (FR-17)."""
    duration = int(voice.get("duration") or 0)
    if duration > config.MAX_VOICE_SECONDS:
        minutes = config.MAX_VOICE_SECONDS // 60
        reply = (f"Голосовое длиннее {minutes} минут — не беру, чтобы не жечь деньги. "
                 "Скажи короче или напиши текстом.")
        db.mark_item(item_id, status="done", reply_text=reply)
        telegram.send_message(reply, chat_id)
        return

    progress_id = telegram.send_message("Расшифровываю…", chat_id)
    try:
        transcript = transcribe.telegram_voice(voice.get("file_id", ""))
    except transcribe.TranscriptionError as e:
        logger.error("bot: transcription failed for item %s: %s", item_id, e)
        db.mark_item(item_id, status="new", error=str(e)[:500])
        _reply(chat_id, progress_id, "Не смогла расшифровать — сохранила, попробую позже.")
        return
    if not transcript:
        reply = "Не разобрала, повтори, пожалуйста."
        db.mark_item(item_id, status="done", reply_text=reply)
        _reply(chat_id, progress_id, reply)
        return

    db.set_transcript(item_id, transcript)
    prefix = f"«{transcript[:1500]}»\n\n"
    _process_text(scope, item_id, transcript, chat_id, progress_id=progress_id, prefix=prefix)


def _process_text(scope: UserScope, item_id: int, text: str, chat_id, *,
                  progress_id: int | None = None, prefix: str = "") -> None:
    """The same pipeline for typed text and transcripts: cheap path first (FR-6),
    then the agent."""
    if text.startswith("/"):
        reply = _command(scope, text)
        db.mark_item(item_id, status="done", reply_text=reply)
        _reply(chat_id, progress_id, prefix + reply)
        return

    if _URL_RE.match(text):
        ctx = ToolContext(inbox_item_id=item_id)
        _, note_id, result = links_tool.save(scope, ctx, text)
        if note_id:
            reply = f"Сохранила ссылку: {result.title or text}"
            if result.summary:
                reply += f"\n{result.summary[:300]}"
        else:
            why = (result.error or f"HTTP {result.status}") if result else "не похоже на ссылку"
            reply = f"Ссылку записала, но страница не открылась ({why}). Заметку не создала."
        db.mark_item(item_id, status="done", reply_text=reply)
        _reply(chat_id, progress_id, prefix + reply, _keyboard(scope, item_id, ctx))
        return

    _run_agent(scope, item_id, text, chat_id, progress_id=progress_id, prefix=prefix)


def _run_agent(scope: UserScope, item_id: int, text: str, chat_id, *,
               progress_id: int | None = None, prefix: str = "") -> None:
    if progress_id is None:
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
        _reply(chat_id, progress_id, prefix + reply)
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("bot: agent failed for item %s", item_id)
        db.mark_item(item_id, status="failed", error=str(e)[:500])
        _reply(chat_id, progress_id, prefix + "Не смогла разобрать — модель не ответила. Сообщение "
                                              "сохранила, попробую позже.")
        return

    suggestions = result.ctx.suggestions
    db.mark_item(item_id, status="done", reply_text=result.reply,
                 suggestions=suggestions if suggestions else None)
    _reply(chat_id, progress_id, prefix + result.reply, _keyboard(scope, item_id, result.ctx))


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
        return prefs_tool.MEMORY_SHOW.fn(scope, ToolContext())
    return "Такой команды нет. /help — что умею."


# --- Keyboards ---------------------------------------------------------------

def _list_rows(scope: UserScope, list_id: int) -> list[list[dict]]:
    """One button per line (FR-49). Telegram caps a keyboard at 100 buttons; we
    stop at MAX_BUTTON_LINES and say so on a last, inert-looking row."""
    items = db.list_items(scope.user_id, list_id)
    rows = []
    for item in items[:lists_tool.MAX_BUTTON_LINES]:
        box = "☑" if item["checked_at"] else "☐"
        rows.append([{"text": f"{box} {item['text']}"[:40], "callback_data": f"li:{item['id']}"}])
    if len(items) > lists_tool.MAX_BUTTON_LINES:
        rows.append([{"text": f"… ещё {len(items) - lists_tool.MAX_BUTTON_LINES}, скажи «покажи текстом»",
                      "callback_data": "noop"}])
    return rows


def _keyboard(scope: UserScope, item_id: int, ctx: ToolContext) -> dict | None:
    rows = []
    if ctx.render_list_id:
        rows += _list_rows(scope, ctx.render_list_id)
    if ctx.created_note_ids:
        rows.append([{"text": "✖︎ Не туда", "callback_data": f"undo:{ctx.created_note_ids[-1]}"}])
    if ctx.created_list_item_ids:
        ids = ",".join(str(i) for i in ctx.created_list_item_ids[-5:])
        rows.append([{"text": "✖︎ Не туда (убрать из списка)", "callback_data": f"undo:l:{ids}"}])
    if ctx.created_reminder_ids:
        rows.append([{"text": "✖︎ Отменить напоминание", "callback_data": f"undo:r:{ctx.created_reminder_ids[-1]}"}])
    for n, s in enumerate(ctx.suggestions[:3]):
        rows.append([{"text": s["label"][:40], "callback_data": f"sg:{item_id}:{n}"}])
    return {"inline_keyboard": rows} if rows else None


# --- Callbacks ---------------------------------------------------------------

def _handle_callback(update_id, callback: dict) -> None:
    message = callback.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    scope = _scope_for(chat_id)
    if scope is None:
        return

    data = callback.get("data") or ""
    action, _, rest = data.partition(":")
    callback_id = callback.get("id", "")

    if action == "li" and rest.isdigit():
        _toggle_list_line(scope, callback_id, message, int(rest))
        return

    telegram.answer_callback(callback_id)

    if action == "noop":
        return

    if action == "undo":
        _undo(scope, rest, chat_id)
        return

    if action == "rm":
        _reminder_button(scope, rest, message, chat_id)
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


def _toggle_list_line(scope: UserScope, callback_id: str, message: dict, item_id: int) -> None:
    """FR-49: the tap flips the line and only the buttons are re-drawn, on the same
    message. Rows that are not list lines (undo, suggestions) are kept."""
    row = db.set_list_item_checked(scope.user_id, item_id, None)
    if not row:
        telegram.answer_callback(callback_id, "Эта строка уже не твоя или удалена.")
        return
    telegram.answer_callback(callback_id, "✓" if row["checked_at"] else "↩")
    existing = (message.get("reply_markup") or {}).get("inline_keyboard") or []
    others = [r for r in existing if not any(str(b.get("callback_data", "")).startswith("li:") for b in r)]
    telegram.edit_reply_markup(message.get("message_id"), message.get("chat", {}).get("id"),
                               {"inline_keyboard": _list_rows(scope, row["list_id"]) + others})


def _undo(scope: UserScope, rest: str, chat_id) -> None:
    kind, _, ids = rest.partition(":")
    if rest.isdigit():                      # stage-1 form: a note
        kind, ids = "n", rest
    if kind == "n" and ids.isdigit():
        note_id = int(ids)
        if db.soft_delete_note(scope.user_id, note_id):
            db.add_correction(scope.user_id, None, f"saved note #{note_id}", None)
            telegram.send_message("Убрала. Скажи, куда это на самом деле — запомню.", chat_id)
        else:
            telegram.send_message("Эту запись уже убрала раньше.", chat_id)
        return
    if kind == "l":
        removed = sum(1 for i in ids.split(",") if i.isdigit() and db.delete_list_item(scope.user_id, int(i)))
        if removed:
            db.add_correction(scope.user_id, None, f"added {removed} list line(s)", None)
            telegram.send_message("Убрала из списка. Скажи, куда это на самом деле — запомню.", chat_id)
        else:
            telegram.send_message("Эти строки уже убрала раньше.", chat_id)
        return
    if kind == "r" and ids.isdigit():
        row = db.get_reminder(scope.user_id, int(ids))
        if row and row["status"] == "scheduled" and db.set_reminder_status(scope.user_id, int(ids), "cancelled"):
            telegram.send_message(f"Отменила напоминание: {row['text']}.", chat_id)
        else:
            telegram.send_message("Это напоминание уже не активно.", chat_id)


def _reminder_button(scope: UserScope, rest: str, message: dict, chat_id) -> None:
    """FR-21: сделано / +1 час / завтра under a delivered reminder."""
    what, _, id_part = rest.partition(":")
    if not id_part.isdigit():
        return
    reminder_id = int(id_part)
    row = db.get_reminder(scope.user_id, reminder_id)
    if not row:
        telegram.send_message("Это напоминание не найдено.", chat_id)
        return
    message_id = message.get("message_id")
    zone = ZoneInfo(scope.tz)
    now = datetime.now(timezone.utc).astimezone(zone)
    if what == "done":
        db.set_reminder_status(scope.user_id, reminder_id, "done")
        _reply(chat_id, message_id, f"✓ {row['text']}")
    elif what == "snooze":
        fire_at = now + timedelta(hours=1)
        db.set_reminder_status(scope.user_id, reminder_id, "scheduled", fire_at=fire_at)
        _reply(chat_id, message_id, f"⏰ {row['text']} — напомню {timeparse.fmt(fire_at, now)}.")
    elif what == "tomorrow":
        fire_at = (now + timedelta(days=1)).replace(hour=timeparse.DEFAULT_HOUR, minute=0,
                                                    second=0, microsecond=0)
        db.set_reminder_status(scope.user_id, reminder_id, "scheduled", fire_at=fire_at)
        _reply(chat_id, message_id, f"⏰ {row['text']} — напомню {timeparse.fmt(fire_at, now)}.")


def _reply(chat_id, progress_id: int | None, text: str, markup: dict | None = None) -> None:
    """Rewrite the placeholder, or send fresh if there was none."""
    if progress_id:
        telegram.edit_message(progress_id, text, chat_id, markup)
    else:
        telegram.send_message(text, chat_id, markup)

