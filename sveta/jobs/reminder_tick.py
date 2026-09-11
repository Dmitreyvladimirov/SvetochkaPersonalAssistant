"""The reminder tick (FR-20, FR-21). Lives inside the web process, not in a cron
(SPEC.md §7): a daemon thread wakes every TICK_SECONDS, asks the database for due
rows under FOR UPDATE SKIP LOCKED and sends each one. A row is marked sent only
after Telegram accepted the message, so an outage delays a reminder instead of
losing it. The thread never dies on an exception: it logs and sleeps.

Redeploy safety (the stage DoD): nothing lives in memory. A reminder created on
the old container is a Postgres row; the new container's first tick picks it up."""
import logging
import threading
import time

from sveta.core import config, db, telegram

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_last_ok: float = 0.0


def keyboard(reminder_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✓ сделано", "callback_data": f"rm:done:{reminder_id}"},
        {"text": "+1 час", "callback_data": f"rm:snooze:{reminder_id}"},
        {"text": "завтра", "callback_data": f"rm:tomorrow:{reminder_id}"},
    ]]}


def deliver(row: dict) -> bool:
    """One reminder → one message. True only if Telegram returned a message_id."""
    message_id = telegram.send_message(f"⏰ {row['text']}", row["telegram_chat_id"],
                                       keyboard(row["id"]))
    if message_id is None:
        logger.error("tick: reminder %s not delivered — will retry next tick", row["id"])
        return False
    return True


def tick() -> int:
    """One pass. Returns how many reminders were sent. Safe to call from tests."""
    return db.deliver_due_reminders(deliver)


def _loop(period: float) -> None:
    global _last_ok
    logger.info("tick: started, period %ss", period)
    while True:
        try:
            sent = tick()
            _last_ok = time.monotonic()
            if sent:
                logger.info("tick: sent %d reminder(s)", sent)
        except Exception:  # noqa: BLE001 — the loop must outlive any single failure
            logger.exception("tick: pass failed")
        time.sleep(period)


def start() -> bool:
    """Start the daemon thread once. SVETA_TICK_SECONDS=0 disables it (tests, and
    any role that must not send reminders)."""
    global _thread
    period = config.TICK_SECONDS
    if period <= 0:
        logger.info("tick: disabled (SVETA_TICK_SECONDS=%s)", period)
        return False
    if _thread and _thread.is_alive():
        return True
    _thread = threading.Thread(target=_loop, args=(period,), name="reminder-tick", daemon=True)
    _thread.start()
    return True


def alive() -> bool:
    """For /health: the thread exists and its last successful pass is recent."""
    if not (_thread and _thread.is_alive()):
        return False
    return (time.monotonic() - _last_ok) < max(3 * config.TICK_SECONDS, 60)
