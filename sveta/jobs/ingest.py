"""SERVICE_TYPE=ingest — the cron entry point for RSS (FR-33).

    python -m sveta.jobs.ingest [--dry-run]

Every enabled source of every user is polled; new items land in source_items
and the morning brief shows up to three of them. --dry-run parses and counts
without writing."""
import argparse
import logging
import sys

from sveta.core import config, db, fetch
from sveta.jobs import rss

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sveta.jobs.ingest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config.validate_secrets()
    try:
        db.ping()
    except Exception as e:  # noqa: BLE001
        logger.error("ingest: database unreachable: %s", e)
        return 1
    if args.dry_run:
        for source in db.all_enabled_sources():
            body, _, error = fetch.get_bytes(source["url"])
            if error:
                print(f"{source['url']}: {error}")
                continue
            try:
                title, items = rss.parse(body, source["url"])
                print(f"{source['url']}: {title!r}, {len(items)} item(s)")
            except ValueError as e:
                print(f"{source['url']}: {e}")
        return 0
    polled, new = rss.poll_all()
    logger.info("ingest: %d source(s) polled, %d new item(s)", polled, new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
