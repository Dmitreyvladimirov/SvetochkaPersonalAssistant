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
        self.llm_calls = []
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

    # inbox
    def claim_update(self, update_id, user_id, *, message_id=None, kind="text", raw_text=None,
                     file_id=None, duration_sec=None):
        if update_id is not None:
            if update_id in self._update_ids:
                return None
            self._update_ids.add(update_id)
        iid = self._next()
        self.inbox[iid] = {"id": iid, "user_id": user_id, "tg_update_id": update_id, "kind": kind,
                           "raw_text": raw_text, "status": "new", "reply_text": None,
                           "suggestions": None, "error": None}
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
        rows = [r for r in self.inbox.values()
                if r["user_id"] == user_id and r["raw_text"] and r["reply_text"]]
        return [{"raw_text": r["raw_text"], "reply_text": r["reply_text"]} for r in rows[-limit:]]

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
                           "deleted_at": None}
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
        self.corrections.append({"user_id": user_id, "did": did, "should_have": should_have})

    def recent_corrections(self, user_id, limit=5):
        return [c for c in self.corrections if c["user_id"] == user_id][-limit:]

    # cost
    def record_llm_call(self, user_id, inbox_item_id, purpose, model, usage, cost_usd, latency_ms):
        self.llm_calls.append({"user_id": user_id, "purpose": purpose, "cost_usd": cost_usd})

    def spend_today(self, user_id):
        return sum(c["cost_usd"] for c in self.llm_calls if c["user_id"] == user_id)

    # stage-0 API, kept so app tests still work
    def init_db(self):
        pass

    def seed_users(self, chat_ids, tz):
        return 0

    def ping(self):
        return {"ok": True, "users": len(self.users)}


NAMES = [n for n in dir(FakeDB) if not n.startswith("_") and n not in ("add_user",)]


def install(monkeypatch) -> FakeDB:
    """Replace every function of sveta.core.db with the fake's bound methods."""
    from sveta.core import db
    fake = FakeDB()
    for name in NAMES:
        monkeypatch.setattr(db, name, getattr(fake, name))
    return fake
