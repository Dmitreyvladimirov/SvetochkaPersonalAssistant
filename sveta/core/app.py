"""The web service: a health check, the Telegram webhook, and the startup sequence.

Kept thin on purpose — this is the one component whose downtime is visible from
the phone. Everything slow happens in a thread behind the webhook.
"""
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from sveta.core import bot, config, db, telegram

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _register_webhook() -> None:
    """Point Telegram at this deployment. Idempotent and best-effort: Railway hands
    us the public domain, the secret comes from the environment, so nobody has to
    paste the bot token anywhere. A failure is logged, not fatal — /health still
    has to answer so the deployment is not rolled back over a Telegram hiccup."""
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not domain or not config.WEBHOOK_SECRET:
        logger.warning("webhook: RAILWAY_PUBLIC_DOMAIN or secret missing — not registering")
        return
    url = f"https://{domain}{WEBHOOK_PATH}"
    if telegram.set_webhook(url, config.WEBHOOK_SECRET):
        logger.info("webhook: registered at %s", url)
    else:
        logger.error("webhook: setWebhook failed — see telegram errors above")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Order matters: a missing variable must fail before any connection is tried,
    # so the log says "Missing required env vars" rather than a driver traceback.
    config.validate_secrets()
    db.init_db()
    db.seed_users(config.ALLOWED_CHAT_IDS, config.TZ)
    _register_webhook()
    yield


app = FastAPI(title="Svetochka", docs_url=None, redoc_url=None, lifespan=_lifespan)

# A fixed path, on purpose. The first design (copied from JobScraper) carried the
# secret in the path as a second layer — and every access log, Railway's HTTP log
# included, printed it on every request (found 2026-09-03). The header is the
# layer Telegram designed for exactly this, and headers never reach access logs.
WEBHOOK_PATH = "/tg/webhook"


@app.get("/health")
def health():
    """200 with the commit hash when the service and its database are up; 503
    otherwise. Railway's healthcheck reads this, so a dead database keeps a
    broken deployment from replacing a working one."""
    commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev")[:12]
    try:
        database = db.ping()
    except Exception as e:  # noqa: BLE001 — anything here means "not healthy"
        logger.error("health: database check failed: %s", e)
        return JSONResponse({"ok": False, "commit": commit, "db": {"ok": False}}, status_code=503)
    return {"ok": True, "commit": commit, "db": database}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Two independent layers (SPEC.md §6.5): the secret Telegram echoes in
    X-Telegram-Bot-Api-Secret-Token, and the users table inside bot._scope_for().
    An unset secret answers 503 rather than falling open — this is the one route
    reachable without a login. 200 is returned before any work starts: Telegram
    redelivers anything not acknowledged within seconds."""
    if not config.WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="SVETA_WEBHOOK_SECRET not set")
    header = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(header, config.WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Bad webhook secret")
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Malformed update")
    bot.start(update)
    return JSONResponse({"ok": True})
