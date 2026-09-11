"""RSS 2.0 and Atom parse; a second poll adds nothing; a bad feed leaves an error."""
from datetime import datetime, timezone

import pytest

from sveta.core import fetch
from sveta.jobs import rss
from tests.fakedb import install

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><title>First &amp; best</title><link>https://blog.example/1</link><guid>tag:1</guid>
<pubDate>Wed, 09 Sep 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Second</title><link>/relative/2</link></item>
<item><title>No link no guid</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom feed</title>
<entry><id>urn:a1</id><title>Entry one</title><link rel="alternate" href="https://a.example/1"/>
<link rel="enclosure" href="https://a.example/1.mp3"/><published>2026-09-10T08:00:00Z</published><summary>Sum</summary></entry>
<entry><id>urn:a2</id><title>Entry two</title><updated>2026-09-10T09:30:00+02:00</updated><content type="html">&lt;p&gt;Body&lt;/p&gt;</content></entry>
</feed>"""


def test_parse_rss():
    title, items = rss.parse(RSS, "https://blog.example/feed")
    assert title == "Blog" and len(items) == 2
    assert items[0] == {"external_id": "tag:1", "url": "https://blog.example/1", "title": "First & best",
                        "summary": "Hello world", "published_at": datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)}
    assert items[1]["external_id"] == "https://blog.example/relative/2" and items[1]["published_at"] is None


def test_parse_atom():
    title, items = rss.parse(ATOM, "https://a.example/")
    assert title == "Atom feed" and [i["external_id"] for i in items] == ["urn:a1", "urn:a2"]
    assert items[0]["url"] == "https://a.example/1" and items[0]["summary"] == "Sum"
    assert items[1]["url"] is None and items[1]["summary"] == "Body"
    assert items[1]["published_at"].utcoffset().total_seconds() == 7200


@pytest.mark.parametrize("body", [b"<html><body>not a feed</body></html>", b"garbage <<", b"",
                                  b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel><title>&lol;</title></channel></rss>'])
def test_not_a_feed_raises(body):
    with pytest.raises(ValueError):
        rss.parse(body)


def test_poll_dedups_and_records_errors(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111")
    sid, _ = fake.add_source(1, "https://blog.example/feed")
    monkeypatch.setattr(fetch, "get_bytes", lambda url: (RSS, {"etag": "e1"}, None))
    assert rss.poll(fake.sources[sid]) == 2
    assert rss.poll(fake.sources[sid]) == 0
    assert fake.sources[sid]["title"] == "Blog" and fake.sources[sid]["last_error"] is None
    assert len(fake.source_items) == 2

    monkeypatch.setattr(fetch, "get_bytes", lambda url: (None, {}, "refused: private"))
    assert rss.poll(fake.sources[sid]) == 0
    assert fake.sources[sid]["last_error"] == "refused: private"

    monkeypatch.setattr(fetch, "get_bytes", lambda url: (b"<html/>", {}, None))
    rss.poll(fake.sources[sid])
    assert fake.sources[sid]["last_error"].startswith("unknown feed root")


def test_poll_all_survives_one_bad_source(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111")
    fake.add_source(1, "https://a.example/feed")
    fake.add_source(1, "https://b.example/feed")

    def get_bytes(url):
        if "a.example" in url:
            raise RuntimeError("boom")
        return ATOM, {}, None
    monkeypatch.setattr(fetch, "get_bytes", get_bytes)
    assert rss.poll_all() == (2, 2)


def test_items_never_cross_users(monkeypatch):
    fake = install(monkeypatch)
    fake.add_user("111"); fake.add_user("222")
    sid, _ = fake.add_source(1, "https://blog.example/feed")
    monkeypatch.setattr(fetch, "get_bytes", lambda url: (RSS, {}, None))
    rss.poll(fake.sources[sid])
    from sveta.core import db
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert len(db.recent_source_items(1, since)) == 2 and db.recent_source_items(2, since) == []
