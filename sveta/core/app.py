"""The web service. In stage 0: a health check and the startup sequence. The
webhook and the agent arrive in stage 1 (SPEC.md §11).

Kept thin on purpose — this is the one component whose downtime is visible from
the phone.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sveta.core import config, db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Order matters: a missing variable must fail before any connection is tried,
    # so the log says "Missing required env vars" rather than a driver traceback.
    config.validate_secrets()
    db.init_db()
    db.seed_users(config.ALLOWED_CHAT_IDS, config.TZ)
    yield


app = FastAPI(title="Svetochka", docs_url=None, redoc_url=None, lifespan=_lifespan)


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
