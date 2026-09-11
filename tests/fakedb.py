"""An in-memory stand-in for sveta.core.db with the same function signatures.

It filters by user_id exactly where the SQL does, so the cross-user isolation test
is meaningful: if a tool ever forgot to pass scope.user_id, the fake would return
another user's rows just as Postgres would.
"""
from datetime import datetime, timezone


class FakeDB:
    def __init__(self):
        self.users = {}            # chat_id -> row
        self.inbox = {}            # id -> row
        self.notes = {}            # id -> row
        self.prefs = {}            # (user_id, key) -> value
        self.corrections = []
        self.facts = {}            # id -> row
        self.oauth = {}            # (user_id, provider) -> row
        self.mail = {}             # (user_id, gmail_id) -> row
        self.llm_calls = []
        self.lists = {}            # id -> row
        self.list_items = {}       # id -> row
        self.reminders = {}        # id -> row
        self.links = {}            # id -> row
        self.digests = {}          # id -> row
        self.sources = {}          # id -> row
        self.source_items = {}     # id -> row
        self._seq = 0
        self._update_ids = set()

    def _next(self):
        self._seq += 1
        return self._seq

    # users
    def add_user(self, chat_id, tz="Asia/Jerusalem"):
        uid = self._next()
        self.users[str(chat_id)] = {"id": uid, "telegram_chat_id": str(chat_id), "tz": tz, "status": "active"}
        return uid

    def get_user_by_chat(self, chat_id):
        return self.users.get(str(chat_id))

    def get_user_by_id(self, user_id):
        return next((dict(u) for u in self.users.values() if u["id"] == user_id), None)

    # inbox
    def claim_update(self, update_id, user_id, *, message_id=None, kind="text", raw_text=None,
                     file_id=None, duration_sec=None, file_name=None, file_mime=None):
        if update_id is not None:
            if update_id in self._update_ids:
                return None
            self._update_ids.add(update_id)
        iid = self._next()
        self.inbox[iid] = {"id": iid, "user_id": user_id, "tg_update_id": update_id, "kind": kind,
                           "raw_text": raw_text, "status": "new", "reply_text": None,
                           "suggestions": None, "error": None,
                           "received_at": datetime.now(timezone.utc),
                           "file_id": file_id, "file_name": file_name, "file_mime": file_mime}
        return iid

    def mark_item(self, item_id, *, status, error=None, reply_text=None, suggestions=None):
        row = self.inbox[item_id]
        row["status"] = status
        row["error"] = error
        if reply_text is not None:
            row["reply_text"] = reply_text
        if suggestions is not None:
            row["suggestions"] = suggestions

    def get_item(self, user_id, item_id):
        row = self.inbox.get(item_id)
        return dict(row) if row and row["user_id"] == user_id else None

    def recent_exchanges(self, user_id, limit=8):
        # Command replies ("/connect" → "Google — не подключён") are status screens,
        # not conversation: fed into the history they teach the model stale facts.
        rows = [r for r in self.inbox.values()
                if r["user_id"] == user_id and (r["raw_text"] or r.get("transcript")) and r["reply_text"]
                and r["kind"] in ("text", "voice")
                and not (r["raw_text"] or r.get("transcript") or "").startswith("/")]
        return [{"raw_text": r["raw_text"] or r.get("transcript"), "reply_text": r["reply_text"]}
                for r in rows[-limit:]]

    def set_transcript(self, item_id, transcript):
        self.inbox[item_id]["transcript"] = transcript

    # notes
    def create_note(self, user_id, body, *, title=None, tags=None, project=None,
                    source="telegram", source_ref=None, inbox_item_id=None):
        if source_ref:
            for r in self.notes.values():
                if r["user_id"] == user_id and r["source"] == source and r["source_ref"] == source_ref:
                    r["deleted_at"] = None
                    return r["id"]
        nid = self._next()
        self.notes[nid] = {"id": nid, "user_id": user_id, "title": title, "body": body,
                           "tags": tags or [], "project": project, "source": source,
                           "source_ref": source_ref, "created_at": datetime.now(timezone.utc),
                           "inbox_item_id": inbox_item_id, "deleted_at": None}
        return nid

    def search_notes(self, user_id, query, *, limit=5, source=None):
        q = query.lower()
        out = [r for r in self.notes.values()
               if r["user_id"] == user_id and r["deleted_at"] is None
               and (source is None or r["source"] == source)
               and any(w in ((r["title"] or "") + " " + r["body"]).lower() for w in q.split())]
        return out[:limit]

    def recent_notes(self, user_id, *, limit=10, project=None):
        out = [r for r in self.notes.values()
               if r["user_id"] == user_id and r["deleted_at"] is None
               and (project is None or r["project"] == project)]
        return list(reversed(out))[:limit]

    def note_file(self, user_id, note_id):
        note = self.notes.get(note_id)
        if not note or note["user_id"] != user_id:
            return None
        item = self.inbox.get(note.get("inbox_item_id"))
        if not item or not item.get("file_id"):
            return None
        return {"file_id": item["file_id"], "file_name": item.get("file_name"),
                "file_mime": item.get("file_mime"), "kind": item["kind"]}

    def notes_with_files(self, user_id, note_ids):
        return {n for n in note_ids if self.note_file(user_id, n)}

    def get_note(self, user_id, note_id):
        r = self.notes.get(note_id)
        return dict(r) if r and r["user_id"] == user_id else None

    def items_between(self, user_id, since, before_item_id):
        return sum(1 for r in self.inbox.values()
                   if r["user_id"] == user_id and r["received_at"] >= since and r["id"] < before_item_id)

    def soft_delete_note(self, user_id, note_id):
        r = self.notes.get(note_id)
        if r and r["user_id"] == user_id and r["deleted_at"] is None:
            r["deleted_at"] = datetime.now(timezone.utc)
            return True
        return False

    # preferences / corrections
    def set_preference(self, user_id, key, value, *, set_via="chat"):
        self.prefs[(user_id, key)] = value

    def delete_preference(self, user_id, key):
        return self.prefs.pop((user_id, key), None) is not None

    def list_preferences(self, user_id):
        return {k: v for (u, k), v in sorted(self.prefs.items()) if u == user_id}

    def add_correction(self, user_id, inbox_item_id, did, should_have=None):
        self.corrections.append({"id": self._next(), "user_id": user_id, "did": did,
                                 "should_have": should_have, "created_at": datetime.now(timezone.utc)})

    def recent_corrections(self, user_id, limit=5):
        return list(reversed([c for c in self.corrections if c["user_id"] == user_id][-limit:]))

    def open_correction(self, user_id, *, max_age_minutes=15):
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        for c in reversed(self.corrections):
            if c["user_id"] == user_id and c["should_have"] is None and c["created_at"] >= cutoff:
                return dict(c)
        return None

    def fill_correction(self, user_id, correction_id, should_have):
        for c in self.corrections:
            if c["id"] == correction_id and c["user_id"] == user_id and c["should_have"] is None:
                c["should_have"] = should_have
                return True
        return False

    # facts
    def remember_fact(self, user_id, subject, predicate, obj, *, replaces=True, source_note_id=None):
        closed = []
        same = None
        for f in self.facts.values():
            if (f["user_id"] == user_id and f["valid_to"] is None and f["subject"].lower() == subject.lower()
                    and f["predicate"].lower() == predicate.lower()):
                if f["object"].lower() == obj.lower():
                    same = f
                elif replaces:
                    f["valid_to"] = datetime.now(timezone.utc)
                    closed.append(f["object"])
        if same:
            return same["id"], closed
        fid = self._next()
        self.facts[fid] = {"id": fid, "user_id": user_id, "subject": subject, "predicate": predicate,
                           "object": obj, "valid_from": datetime.now(timezone.utc), "valid_to": None}
        return fid, closed

    def recall_facts(self, user_id, topic, *, include_closed=False, limit=20):
        t = topic.lower()
        rows = [dict(f) for f in self.facts.values() if f["user_id"] == user_id
                and (include_closed or f["valid_to"] is None)
                and (t in f["subject"].lower() or t in f["object"].lower() or t in f["predicate"].lower())]
        return sorted(rows, key=lambda f: (f["valid_to"] is not None, -f["valid_from"].timestamp()))[:limit]

    def open_facts(self, user_id, *, limit=50):
        return sorted([dict(f) for f in self.facts.values() if f["user_id"] == user_id and f["valid_to"] is None],
                      key=lambda f: (f["subject"], f["valid_from"]))[:limit]

    # cost
    def record_llm_call(self, user_id, inbox_item_id, purpose, model, usage, cost_usd, latency_ms):
        self.llm_calls.append({"user_id": user_id, "purpose": purpose, "cost_usd": cost_usd})

    def spend_today(self, user_id):
        return sum(c["cost_usd"] for c in self.llm_calls if c["user_id"] == user_id)

    # lists
    def find_list(self, user_id, name):
        for r in self.lists.values():
            if r["user_id"] == user_id and r["name"].lower() == name.strip().lower():
                return {"id": r["id"], "name": r["name"], "kind": r["kind"]}
        return None

    def get_or_create_list(self, user_id, name):
        found = self.find_list(user_id, name)
        if found:
            return found
        lid = self._next()
        self.lists[lid] = {"id": lid, "user_id": user_id, "name": name.strip(), "kind": "custom"}
        return {"id": lid, "name": name.strip(), "kind": "custom"}

    def get_list(self, user_id, list_id):
        r = self.lists.get(list_id)
        return {"id": r["id"], "name": r["name"], "kind": r["kind"]} if r and r["user_id"] == user_id else None

    def list_lists(self, user_id):
        out = []
        for l in self.lists.values():
            if l["user_id"] != user_id:
                continue
            items = [i for i in self.list_items.values() if i["list_id"] == l["id"]]
            out.append({"id": l["id"], "name": l["name"], "total": len(items),
                        "unchecked": len([i for i in items if i["checked_at"] is None])})
        return out

    def add_list_items(self, user_id, list_id, texts):
        if self.lists.get(list_id, {}).get("user_id") != user_id:
            return []
        pos = max([i["position"] for i in self.list_items.values() if i["list_id"] == list_id], default=0)
        ids = []
        for t in texts:
            pos += 1
            iid = self._next()
            self.list_items[iid] = {"id": iid, "user_id": user_id, "list_id": list_id, "text": t,
                                    "position": pos, "checked_at": None, "moved_from": None}
            ids.append(iid)
        return ids

    def list_items_(self, user_id, list_id):
        return [dict(i) for i in sorted(self.list_items.values(), key=lambda i: (i["position"], i["id"]))
                if i["list_id"] == list_id and i["user_id"] == user_id]

    def set_list_item_checked(self, user_id, item_id, checked):
        r = self.list_items.get(item_id)
        if not r or r["user_id"] != user_id:
            return None
        if checked is None:
            checked = r["checked_at"] is None
        r["checked_at"] = datetime.now(timezone.utc) if checked else None
        return dict(r)

    def delete_list_item(self, user_id, item_id):
        r = self.list_items.get(item_id)
        if r and r["user_id"] == user_id:
            del self.list_items[item_id]
            return True
        return False

    def move_list_item(self, user_id, item_id, to_list_id):
        r = self.list_items.get(item_id)
        if not r or r["user_id"] != user_id or r["list_id"] == to_list_id:
            return False
        r["moved_from"], r["list_id"] = r["list_id"], to_list_id
        r["position"] = max([i["position"] for i in self.list_items.values() if i["list_id"] == to_list_id], default=0) + 1
        return True

    def unchecked_list_items(self, user_id, list_name):
        found = self.find_list(user_id, list_name)
        return [i for i in self.list_items_(user_id, found["id"]) if i["checked_at"] is None] if found else []

    # reminders
    def create_reminder(self, user_id, text, fire_at, tz, dedup_key, inbox_item_id=None):
        for r in self.reminders.values():
            if r["user_id"] == user_id and r["dedup_key"] == dedup_key:
                if r["status"] != "scheduled":
                    r.update(status="scheduled", fire_at=fire_at, sent_at=None)
                    return r["id"], True
                return r["id"], False
        rid = self._next()
        self.reminders[rid] = {"id": rid, "user_id": user_id, "text": text, "fire_at": fire_at, "tz": tz,
                               "dedup_key": dedup_key, "status": "scheduled", "sent_at": None}
        return rid, True

    def list_reminders(self, user_id, *, status="scheduled", limit=20):
        rows = [dict(r) for r in self.reminders.values() if r["user_id"] == user_id and r["status"] == status]
        return sorted(rows, key=lambda r: r["fire_at"])[:limit]

    def get_reminder(self, user_id, reminder_id):
        r = self.reminders.get(reminder_id)
        return dict(r) if r and r["user_id"] == user_id else None

    def set_reminder_status(self, user_id, reminder_id, status, fire_at=None):
        r = self.reminders.get(reminder_id)
        if not r or r["user_id"] != user_id:
            return False
        r["status"] = status
        if fire_at is not None:
            r["fire_at"] = fire_at
        if status == "scheduled":
            r["sent_at"] = None
        return True

    def deliver_due_reminders(self, deliver, *, limit=20):
        now = datetime.now(timezone.utc)
        due = sorted([r for r in self.reminders.values()
                      if r["status"] == "scheduled" and r["fire_at"] <= now], key=lambda r: r["fire_at"])[:limit]
        sent = 0
        for r in due:
            chat = next((u["telegram_chat_id"] for u in self.users.values() if u["id"] == r["user_id"]), None)
            row = dict(r, telegram_chat_id=chat)
            if deliver(row):
                r["status"], r["sent_at"] = "sent", now
                sent += 1
        return sent

    # links
    def create_link(self, user_id, url, *, inbox_item_id=None, final_url=None, http_status=None,
                    title=None, summary=None, fetch_error=None):
        lid = self._next()
        self.links[lid] = {"id": lid, "user_id": user_id, "url": url, "final_url": final_url,
                           "http_status": http_status, "title": title, "summary": summary,
                           "fetch_error": fetch_error}
        return lid

    # stage 4
    def active_users(self):
        return [{"id": u["id"], "telegram_chat_id": u["telegram_chat_id"], "tz": u["tz"]}
                for u in sorted(self.users.values(), key=lambda u: u["id"]) if u["status"] == "active"]

    def reminders_between(self, user_id, start, end, *, statuses=("scheduled",)):
        rows = [dict(r) for r in self.reminders.values()
                if r["user_id"] == user_id and r["status"] in statuses and start <= r["fire_at"] < end]
        return sorted(rows, key=lambda r: r["fire_at"])

    def reminders_overdue(self, user_id, before):
        rows = [dict(r) for r in self.reminders.values()
                if r["user_id"] == user_id and r["status"] == "sent" and r["sent_at"] and r["sent_at"] < before]
        return sorted(rows, key=lambda r: r["sent_at"])

    def get_digest(self, user_id, kind, for_date):
        for d in self.digests.values():
            if d["user_id"] == user_id and d["kind"] == kind and d["for_date"] == for_date:
                return dict(d)
        return None

    def create_digest(self, user_id, kind, for_date, payload, *, tg_message_id=None, model=None, cost_usd=0.0):
        if self.get_digest(user_id, kind, for_date):
            return None
        did = self._next()
        self.digests[did] = {"id": did, "user_id": user_id, "kind": kind, "for_date": for_date,
                             "payload": dict(payload), "tg_message_id": tg_message_id,
                             "sent_at": datetime.now(timezone.utc) if tg_message_id else None,
                             "model": model, "cost_usd": cost_usd}
        return did

    def set_digest_sent(self, user_id, digest_id, tg_message_id):
        d = self.digests.get(digest_id)
        if d and d["user_id"] == user_id:
            d["tg_message_id"], d["sent_at"] = tg_message_id, datetime.now(timezone.utc)

    def set_digest_reaction(self, user_id, digest_id, reaction):
        d = self.digests.get(digest_id)
        if not d or d["user_id"] != user_id:
            return False
        d["payload"]["reaction"] = reaction
        return True

    def list_sources(self, user_id):
        return [dict(s) for s in self.sources.values() if s["user_id"] == user_id]

    def all_enabled_sources(self):
        return [dict(s) for s in self.sources.values() if s["enabled"]]

    def add_source(self, user_id, url, *, kind="rss", title=None):
        for s in self.sources.values():
            if s["user_id"] == user_id and s["url"] == url:
                return s["id"], False
        sid = self._next()
        self.sources[sid] = {"id": sid, "user_id": user_id, "kind": kind, "url": url, "title": title,
                             "enabled": True, "last_polled_at": None, "last_error": None, "etag": None}
        return sid, True

    def delete_source(self, user_id, source_id):
        s = self.sources.get(source_id)
        if s and s["user_id"] == user_id:
            del self.sources[source_id]
            self.source_items = {k: v for k, v in self.source_items.items() if v["source_id"] != source_id}
            return True
        return False

    def mark_source_polled(self, user_id, source_id, *, error=None, etag=None, title=None):
        s = self.sources[source_id]
        if s["user_id"] != user_id:
            return
        s["last_polled_at"] = datetime.now(timezone.utc)
        s["last_error"] = error
        if etag:
            s["etag"] = etag
        if title:
            s["title"] = title

    def upsert_source_items(self, user_id, source_id, items):
        new = 0
        for it in items:
            if any(r["user_id"] == user_id and r["external_id"] == it["external_id"] for r in self.source_items.values()):
                continue
            iid = self._next()
            self.source_items[iid] = {"id": iid, "user_id": user_id, "source_id": source_id,
                                      "external_id": it["external_id"], "url": it.get("url"),
                                      "title": it.get("title"), "summary": it.get("summary"),
                                      "published_at": it.get("published_at"),
                                      "fetched_at": datetime.now(timezone.utc)}
            new += 1
        return new

    def recent_source_items(self, user_id, since, *, limit=3):
        rows = []
        for r in self.source_items.values():
            if r["user_id"] != user_id:
                continue
            when = r["published_at"] or r["fetched_at"]
            if when >= since:
                src = self.sources.get(r["source_id"], {})
                rows.append({"id": r["id"], "title": r["title"], "url": r["url"],
                             "source_title": src.get("title"), "published_at": when})
        return sorted(rows, key=lambda r: r["published_at"], reverse=True)[:limit]

    def spend_month(self, user_id):
        return self.spend_today(user_id)

    # stage 3
    def save_oauth_token(self, user_id, provider, account_email, refresh_token_enc, *, scopes=None):
        row = {"id": self._next(), "user_id": user_id, "provider": provider, "account_email": account_email,
               "refresh_token": refresh_token_enc, "scopes": scopes, "last_error": None,
               "last_refresh_at": datetime.now(timezone.utc)}
        self.oauth[(user_id, provider)] = row
        return row["id"]

    def get_oauth_token(self, user_id, provider):
        row = self.oauth.get((user_id, provider))
        return dict(row) if row else None

    def mark_oauth_error(self, user_id, provider, error):
        row = self.oauth.get((user_id, provider))
        if row:
            row["last_error"] = error

    def save_mail_messages(self, user_id, messages):
        saved = 0
        for m in messages:
            key = (user_id, m["gmail_id"])
            if key in self.mail:
                continue
            self.mail[key] = {"user_id": user_id, **m}
            saved += 1
        return saved

    def get_mail_message(self, user_id, gmail_id):
        row = self.mail.get((user_id, gmail_id))
        return dict(row) if row else None

    def recent_trip_mail(self, user_id, since):
        return sorted([dict(r) for r in self.mail.values()
                       if r["user_id"] == user_id and r.get("received_at") and r["received_at"] >= since],
                      key=lambda r: r["received_at"], reverse=True)[:50]

    def claim_pending(self, user_id, item_id, pid):
        import threading
        with getattr(self, "_lock", threading.Lock()):
            row = self.inbox.get(item_id)
            if not row or row["user_id"] != user_id:
                return False
            for e in row.get("suggestions") or []:
                if e.get("pid") == pid and not e.get("done"):
                    e["done"] = True
                    return True
            return False

    def release_pending(self, user_id, item_id, pid):
        row = self.inbox.get(item_id)
        if row and row["user_id"] == user_id:
            for e in row.get("suggestions") or []:
                if e.get("pid") == pid:
                    e.pop("done", None)

    def set_note_notion_page(self, user_id, note_id, page_id):
        r = self.notes.get(note_id)
        if r and r["user_id"] == user_id:
            r["notion_page_id"] = page_id

    # stage-0 API, kept so app tests still work
    def init_db(self):
        pass

    def seed_users(self, chat_ids, tz):
        return 0

    def ping(self):
        return {"ok": True, "users": len(self.users)}


NAMES = [n for n in dir(FakeDB) if not n.startswith("_") and n not in ("add_user", "list_items_")]
ALIASES = {"list_items": "list_items_"}  # the dict attribute shadows the method name


def install(monkeypatch) -> FakeDB:
    """Replace every function of sveta.core.db with the fake's bound methods."""
    from sveta.core import db
    fake = FakeDB()
    for name in NAMES:
        monkeypatch.setattr(db, name, getattr(fake, name))
    for db_name, fake_name in ALIASES.items():
        monkeypatch.setattr(db, db_name, getattr(fake, fake_name))
    return fake
