"""The schema is a pure function, so its invariants are unit-testable without a
database. These are the constraints other modules silently depend on — losing one
would not fail a test anywhere else until production."""
import re

from sveta.core.db import schema_statements

STATEMENTS = schema_statements()
SQL = "\n".join(STATEMENTS)
TABLES = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SQL)

EXPECTED_TABLES = {
    "users", "inbox_items", "notes", "lists", "list_items", "reminders", "facts",
    "preferences", "corrections", "entities", "entity_links", "sources", "source_items",
    "links", "mail_messages", "digests", "oauth_tokens", "embeddings", "llm_call",
    "job_queue",
}


def _table_ddl(name: str) -> str:
    return next(s for s in STATEMENTS if f"CREATE TABLE IF NOT EXISTS {name} " in s
                or f"CREATE TABLE IF NOT EXISTS {name}\n" in s or s.strip().startswith(f"CREATE TABLE IF NOT EXISTS {name}"))


def test_twenty_tables_as_specified():
    assert set(TABLES) == EXPECTED_TABLES
    assert len(TABLES) == 20


def test_every_create_is_idempotent():
    for stmt in STATEMENTS:
        head = stmt.strip().split("(")[0]
        if head.upper().startswith(("CREATE TABLE", "CREATE INDEX")):
            assert "IF NOT EXISTS" in head, f"not idempotent: {head.strip()}"


def test_users_comes_first_and_owns_nothing():
    # Every other table references it; it must exist before them and must not
    # reference itself.
    assert TABLES[0] == "users"
    assert "user_id" not in _table_ddl("users")


def test_every_personal_table_carries_a_non_null_user_id():
    # NFR-10: isolation by construction. job_queue is the documented exception —
    # a system-wide job has no user.
    for name in EXPECTED_TABLES - {"users", "job_queue"}:
        ddl = _table_ddl(name)
        assert "user_id INTEGER NOT NULL REFERENCES users(id)" in ddl, name


def test_job_queue_user_is_optional():
    ddl = _table_ddl("job_queue")
    assert "user_id      INTEGER REFERENCES users(id)" in ddl
    assert "user_id INTEGER NOT NULL" not in ddl


def test_unique_constraints_are_scoped_by_user():
    # A UNIQUE that forgets user_id would make one user's note collide with
    # another's. The single allowed global UNIQUE is the Telegram update id.
    for name in EXPECTED_TABLES - {"users"}:
        ddl = _table_ddl(name)
        for constraint in re.findall(r"UNIQUE \(([^)]*)\)", ddl):
            assert constraint.split(",")[0].strip() == "user_id", f"{name}: UNIQUE ({constraint})"


def test_webhook_idempotency_key_is_global():
    # Telegram update ids are global, not per chat: the dedup must be global too.
    assert re.search(r"tg_update_id\s+BIGINT UNIQUE", _table_ddl("inbox_items"))


def test_spec_constraints_present():
    assert "UNIQUE (user_id, source, source_ref)" in _table_ddl("notes")
    assert "UNIQUE (user_id, dedup_key)" in _table_ddl("reminders")
    assert "UNIQUE (user_id, key)" in _table_ddl("preferences")
    assert "UNIQUE (user_id, kind, for_date)" in _table_ddl("digests")
    assert "UNIQUE (user_id, name)" in _table_ddl("lists")
    assert "telegram_chat_id TEXT NOT NULL UNIQUE" in _table_ddl("users")


def test_notes_have_a_russian_fulltext_index():
    assert "to_tsvector('russian'" in SQL and "USING GIN" in SQL


def test_reminder_due_index_is_partial():
    assert "reminders_due_idx" in SQL and "WHERE status = 'scheduled'" in SQL


def test_secrets_are_bytea():
    assert "refresh_token   BYTEA NOT NULL" in _table_ddl("oauth_tokens")
    assert "body_enc    BYTEA" in _table_ddl("mail_messages")


def test_pgvector_is_not_turned_on_yet():
    # Deferred to stage 8 on evidence. If this fails, that decision was revisited on purpose.
    assert "CREATE EXTENSION" not in SQL.upper()
    # \b keeps to_tsvector(...) from matching: the full-text index is not pgvector.
    assert not re.search(r"\bvector\(", SQL, re.IGNORECASE)
