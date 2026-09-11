"""SERVICE_TYPE=digest — the cron entry point for the brief and the review.

    python -m sveta.jobs.digest [--dry-run] [--now 2026-09-12T04:30:00+00:00] [--force brief|review]

Runs every 15 minutes on Railway; each user's own brief/review time decides
whether anything is sent (docs/stages/stage4.md). --dry-run prints instead of
sending and writes nothing (the §10 smoke item). Exit code 0 unless the
database itself is unreachable: a failed user is logged and retried next run."""
import argparse
import logging
import sys
from datetime import datetime, timezone

from sveta.core import config, db
from sveta.jobs import brief

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sveta.jobs.digest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="ISO timestamp to pretend it is (tests, smoke)")
    parser.add_argument("--force", choices=list(brief.KINDS), help="send this kind regardless of the window")
    args = parser.parse_args(argv)

    config.validate_secrets()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        db.ping()
    except Exception as e:  # noqa: BLE001
        logger.error("digest: database unreachable: %s", e)
        return 1
    results = brief.run(now, dry_run=args.dry_run, force=args.force)
    for key, status in sorted(results.items()):
        logger.info("digest: %s → %s", key, status)
    if not results:
        logger.info("digest: nothing due at %s", now.isoformat(timespec="minutes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
