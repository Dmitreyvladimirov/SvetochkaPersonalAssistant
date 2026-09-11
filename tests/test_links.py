"""link_save files a note only when the page answered; every attempt leaves a
links row; the fetch is faked at the module boundary."""
from sveta.core import fetch
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, run
from tests.fakedb import install


def scope(uid=1):
    return UserScope(user_id=uid, chat_id="111", tz="Asia/Jerusalem")


def fake_fetch(monkeypatch, **fields):
    def _get(url):
        return fetch.FetchResult(url=url, **fields)
    monkeypatch.setattr(fetch, "get", _get)


def test_save_with_comment_files_a_note_with_title_and_source(monkeypatch):
    fake = install(monkeypatch)
    fake_fetch(monkeypatch, final_url="https://linear.app/blog/how-we-plan", status=200,
               title="How we plan", summary="Planning at Linear.")
    ctx = ToolContext(inbox_item_id=7)
    out = run(REGISTRY, "link_save", scope(), ctx, {"url": "https://linear.app/blog/how-we-plan", "comment": "почитать про планирование"})
    assert out.startswith("Saved note #") and "How we plan" in out
    note = list(fake.notes.values())[0]
    assert note["source"] == "web" and note["source_ref"] == "https://linear.app/blog/how-we-plan"
    assert note["body"].startswith("почитать про планирование — How we plan\nPlanning at Linear.")
    assert ctx.created_note_ids == [note["id"]]
    assert list(fake.links.values())[0]["http_status"] == 200


def test_404_is_recorded_and_no_note_is_created(monkeypatch):
    fake = install(monkeypatch)
    fake_fetch(monkeypatch, final_url="https://example.com/gone", status=404)
    out = run(REGISTRY, "link_save", scope(), ToolContext(), {"url": "https://example.com/gone", "comment": ""})
    assert "NOT saved" in out and "HTTP 404" in out
    assert fake.notes == {} and list(fake.links.values())[0]["http_status"] == 404


def test_refused_url_is_recorded_with_the_reason(monkeypatch):
    fake = install(monkeypatch)
    fake_fetch(monkeypatch, error="refused: host resolves to 169.254.169.254, which is not public")
    out = run(REGISTRY, "link_save", scope(), ToolContext(), {"url": "http://169.254.169.254/", "comment": ""})
    assert "NOT saved" in out and fake.notes == {}
    assert list(fake.links.values())[0]["fetch_error"].startswith("refused")


def test_non_http_is_an_error_without_a_fetch(monkeypatch):
    fake = install(monkeypatch)
    monkeypatch.setattr(fetch, "get", lambda url: (_ for _ in ()).throw(AssertionError("no fetch")))
    out = run(REGISTRY, "link_save", scope(), ToolContext(), {"url": "file:///etc/passwd", "comment": ""})
    assert out.startswith("Error") and fake.links == {}


def test_link_fetch_reads_without_saving(monkeypatch):
    fake = install(monkeypatch)
    fake_fetch(monkeypatch, status=200, title="T", summary="S")
    out = run(REGISTRY, "link_fetch", scope(), ToolContext(), {"url": "https://example.com/"})
    assert out == "Title: T\nSummary: S" and fake.notes == {} and fake.links == {}
