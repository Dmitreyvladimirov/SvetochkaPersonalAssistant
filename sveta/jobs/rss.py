"""RSS 2.0 and Atom, parsed with the standard library (FR-33). No feedparser: the
two formats the user's feeds actually use are a few dozen lines, and one fewer
dependency is one fewer thing to audit.

`poll()` never raises: a broken feed leaves `last_error` on the source and the
next run tries again."""
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from sveta.core import db, fetch

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_POLL = 50
_ATOM = "{http://www.w3.org/2005/Atom}"


def _text(el, *names) -> str:
    for name in names:
        child = el.find(name)
        if child is not None and (child.text or "").strip():
            return " ".join(child.text.split())
    return ""


def _when(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse(body: bytes, base_url: str = "") -> tuple[str | None, list[dict]]:
    """(feed title, items). An item is {external_id, url, title, summary,
    published_at}. Raises ValueError on something that is not a feed."""
    # A feed never needs a DTD. Refusing one at the expat level — which sees every
    # encoding and every position, unlike a byte scan of the head — closes the
    # entity-expansion and external-entity attacks on the stdlib parser without a
    # defusedxml dependency. The doctype handler fires before any entity is declared.
    _refuse_dtd(body)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as e:
        raise ValueError(f"not XML: {e}") from e
    items: list[dict] = []
    tag = root.tag
    host = urlsplit(base_url).hostname or "feed"

    def external_id(raw: str) -> str:
        # Counter-style guids ('1234') collide across feeds: the dedup key is
        # (user_id, external_id) over all sources, so scope non-URL ids by host.
        raw = raw.strip()
        return raw[:500] if raw.lower().startswith(("http://", "https://")) else f"{host}#{raw}"[:500]

    if tag.lower().endswith("rss") or tag.lower().endswith("rdf"):
        channel = root.find("channel") if root.find("channel") is not None else root
        title = _text(channel, "title")[:200] or None
        for it in channel.iter("item"):
            link = _text(it, "link")
            url = urljoin(base_url, link) if link else None
            guid = _text(it, "guid") or url
            if not guid:
                continue
            items.append({
                "external_id": external_id(guid),
                "url": url[:500] if url else None,
                "title": _text(it, "title")[:200] or None,
                "summary": _strip_html(_text(it, "description"))[:500] or None,
                "published_at": _when(_text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            })
    elif tag == f"{_ATOM}feed":
        title = _text(root, f"{_ATOM}title")[:200] or None
        for it in root.iter(f"{_ATOM}entry"):
            link = ""
            for l in it.findall(f"{_ATOM}link"):
                if l.get("rel", "alternate") == "alternate" and l.get("href"):
                    link = l.get("href")
                    break
            entry_id = _text(it, f"{_ATOM}id") or link
            if not entry_id:
                continue
            items.append({
                "external_id": external_id(entry_id),
                "url": urljoin(base_url, link)[:500] if link else None,
                "title": _text(it, f"{_ATOM}title")[:200] or None,
                "summary": _strip_html(_text(it, f"{_ATOM}summary", f"{_ATOM}content"))[:500] or None,
                "published_at": _when(_text(it, f"{_ATOM}published", f"{_ATOM}updated")),
            })
    else:
        raise ValueError(f"unknown feed root <{root.tag}>")
    return title, items[:MAX_ITEMS_PER_POLL]


def _refuse_dtd(body: bytes) -> None:
    import xml.parsers.expat as expat

    def _refuse(*_):
        raise ValueError("feed declares a DTD; refused")
    scanner = expat.ParserCreate()
    scanner.StartDoctypeDeclHandler = _refuse
    try:
        scanner.Parse(body, True)
    except ValueError:
        raise
    except expat.ExpatError as e:
        raise ValueError(f"not XML: {e}") from e


def _strip_html(text: str) -> str:
    from html import unescape
    import re
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def poll(source: dict) -> int:
    """Fetch one source, store what is new. Returns the number of new items."""
    body, headers, error = fetch.get_bytes(source["url"])
    if error:
        db.mark_source_polled(source["user_id"], source["id"], error=error)
        logger.warning("rss: %s — %s", source["url"], error)
        return 0
    try:
        title, items = parse(body, source["url"])
    except ValueError as e:
        db.mark_source_polled(source["user_id"], source["id"], error=str(e)[:300])
        logger.warning("rss: %s — %s", source["url"], e)
        return 0
    new = db.upsert_source_items(source["user_id"], source["id"], items)
    db.mark_source_polled(source["user_id"], source["id"], error=None, title=title)
    return new


def poll_all() -> tuple[int, int]:
    """(sources polled, new items). The ingest cron's whole job."""
    sources = db.all_enabled_sources()
    new_total = 0
    for source in sources:
        try:
            new_total += poll(source)
        except Exception:  # noqa: BLE001 — one bad feed must not stop the rest
            logger.exception("rss: poll of %s crashed", source.get("url"))
    return len(sources), new_total
