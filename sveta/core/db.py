"""Postgres access and the schema itself (SPEC.md §7.3).

`schema_statements()` is a pure function returning DDL strings; `init_db()` only
executes them. The split is what lets the schema be unit-tested — shape, idempotency,
the constraints everything else silently depends on — without a database in CI.

Migrations are `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`, run on
every boot, no Alembic. Right for a solo project; revisit when more than one person
deploys.

Multi-user by construction (SPEC.md decision 09-03, NFR-10): every table holding
personal data carries `user_id`, every UNIQUE on such a table is composite with it.
The one deliberate exception is `inbox_items.tg_update_id`, which stays globally
unique because Telegram update ids are global — that column is the webhook's
idempotency key, not a per-user fact.
"""
import logging

from sveta.core.config import DATABASE_URL

logger = logging.getLogger(__name__)

# Columns every personal table starts with. Kept as one string so a table cannot
# quietly forget it — the schema test checks for this exact prefix.
_OWNED = "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"


def _conn():
    # Imported here so the schema and every pure helper can be imported (and
    # tested) on a machine with no Postgres driver.
    import psycopg2
    import psycopg2.extras

    if not DATABASE_URL:
        raise EnvironmentError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor,
                            connect_timeout=10)


def schema_statements() -> list[str]:
    """Every DDL statement, in dependency order. Safe to run repeatedly."""
    return [
        # Who Svetochka talks to. v1 has one row, seeded from SVETA_ALLOWED_CHAT_IDS.
        """CREATE TABLE IF NOT EXISTS users (
                id               SERIAL PRIMARY KEY,
                telegram_chat_id TEXT NOT NULL UNIQUE,
                tz               TEXT NOT NULL DEFAULT 'UTC',
                display_name     TEXT,
                status           TEXT NOT NULL DEFAULT 'active',
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",

        # Everything that arrives, stored raw BEFORE any processing (FR-4). If the
        # model, the network or a tool fails, the message still exists and can be
        # reprocessed. tg_update_id UNIQUE is the whole idempotency story (FR-3):
        # a redelivered update dies here, before anything is paid for.
        f"""CREATE TABLE IF NOT EXISTS inbox_items (
                id                SERIAL PRIMARY KEY,
                {_OWNED}
                tg_update_id      BIGINT UNIQUE,
                message_id        BIGINT,
                kind              TEXT NOT NULL,
                raw_text          TEXT,
                file_id           TEXT,
                duration_sec      INTEGER,
                transcript        TEXT,
                received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at      TIMESTAMPTZ,
                status            TEXT NOT NULL DEFAULT 'new',
                error             TEXT
            )""",
        "CREATE INDEX IF NOT EXISTS inbox_items_status_idx ON inbox_items (user_id, status, received_at)",

        # FR-46: every note knows where it came from; the composite UNIQUE is what
        # makes importers (stage 7) re-runnable.
        f"""CREATE TABLE IF NOT EXISTS notes (
                id             SERIAL PRIMARY KEY,
                {_OWNED}
                inbox_item_id  INTEGER REFERENCES inbox_items(id),
                title          TEXT,
                body           TEXT NOT NULL,
                tags           TEXT[] NOT NULL DEFAULT '{{}}',
                project        TEXT,
                source         TEXT NOT NULL DEFAULT 'telegram',
                source_ref     TEXT,
                notion_page_id TEXT,
                pinned         BOOLEAN NOT NULL DEFAULT FALSE,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at     TIMESTAMPTZ,
                UNIQUE (user_id, source, source_ref)
            )""",
        # Russian full-text is the whole search story until pgvector (stage 8).
        """CREATE INDEX IF NOT EXISTS notes_fts_idx ON notes
            USING GIN (to_tsvector('russian', coalesce(title,'') || ' ' || body))""",

        # FR-47…52. A line is arbitrary text; that is the point.
        f"""CREATE TABLE IF NOT EXISTS lists (
                id         SERIAL PRIMARY KEY,
                {_OWNED}
                name       TEXT NOT NULL,
                kind       TEXT NOT NULL DEFAULT 'custom',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, name)
            )""",
        f"""CREATE TABLE IF NOT EXISTS list_items (
                id         SERIAL PRIMARY KEY,
                {_OWNED}
                list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
                text       TEXT NOT NULL,
                position   INTEGER NOT NULL DEFAULT 0,
                checked_at TIMESTAMPTZ,
                moved_from INTEGER REFERENCES lists(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        "CREATE INDEX IF NOT EXISTS list_items_list_idx ON list_items (list_id, position)",

        f"""CREATE TABLE IF NOT EXISTS reminders (
                id                SERIAL PRIMARY KEY,
                {_OWNED}
                inbox_item_id     INTEGER REFERENCES inbox_items(id),
                text              TEXT NOT NULL,
                fire_at           TIMESTAMPTZ NOT NULL,
                tz                TEXT,
                rrule             TEXT,
                status            TEXT NOT NULL DEFAULT 'scheduled',
                dedup_key         TEXT NOT NULL,
                sent_at           TIMESTAMPTZ,
                snooze_until      TIMESTAMPTZ,
                calendar_event_id TEXT,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, dedup_key)
            )""",
        # The scheduler's only query. Partial: 'sent' rows accumulate forever.
        """CREATE INDEX IF NOT EXISTS reminders_due_idx ON reminders (fire_at)
            WHERE status = 'scheduled'""",

        # FR-45: a validity window instead of overwriting. "The doctor changed" closes
        # the old fact; it does not erase it.
        f"""CREATE TABLE IF NOT EXISTS facts (
                id             SERIAL PRIMARY KEY,
                {_OWNED}
                subject        TEXT NOT NULL,
                predicate      TEXT NOT NULL,
                object         TEXT NOT NULL,
                valid_from     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_to       TIMESTAMPTZ,
                source_note_id INTEGER REFERENCES notes(id),
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        "CREATE INDEX IF NOT EXISTS facts_subject_idx ON facts (user_id, subject) WHERE valid_to IS NULL",

        # FR-58 and the persona knobs of §6.4.
        f"""CREATE TABLE IF NOT EXISTS preferences (
                id         SERIAL PRIMARY KEY,
                {_OWNED}
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                set_via    TEXT NOT NULL DEFAULT 'chat',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, key)
            )""",

        # FR-15: what the "Не туда" button produces.
        f"""CREATE TABLE IF NOT EXISTS corrections (
                id            SERIAL PRIMARY KEY,
                {_OWNED}
                inbox_item_id INTEGER REFERENCES inbox_items(id),
                did           TEXT NOT NULL,
                should_have   TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",

        f"""CREATE TABLE IF NOT EXISTS entities (
                id             SERIAL PRIMARY KEY,
                {_OWNED}
                kind           TEXT NOT NULL,
                name           TEXT NOT NULL,
                aliases        TEXT[] NOT NULL DEFAULT '{{}}',
                notion_page_id TEXT,
                last_seen_at   TIMESTAMPTZ,
                UNIQUE (user_id, kind, name)
            )""",
        f"""CREATE TABLE IF NOT EXISTS entity_links (
                {_OWNED}
                entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                object_type TEXT NOT NULL,
                object_id   INTEGER NOT NULL,
                PRIMARY KEY (entity_id, object_type, object_id)
            )""",

        f"""CREATE TABLE IF NOT EXISTS sources (
                id             SERIAL PRIMARY KEY,
                {_OWNED}
                kind           TEXT NOT NULL,
                url            TEXT NOT NULL,
                title          TEXT,
                enabled        BOOLEAN NOT NULL DEFAULT TRUE,
                last_polled_at TIMESTAMPTZ,
                last_error     TEXT,
                etag           TEXT,
                UNIQUE (user_id, url)
            )""",
        f"""CREATE TABLE IF NOT EXISTS source_items (
                id           SERIAL PRIMARY KEY,
                {_OWNED}
                source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                external_id  TEXT NOT NULL,
                url          TEXT,
                title        TEXT,
                summary      TEXT,
                published_at TIMESTAMPTZ,
                fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, external_id)
            )""",

        f"""CREATE TABLE IF NOT EXISTS links (
                id            SERIAL PRIMARY KEY,
                {_OWNED}
                inbox_item_id INTEGER REFERENCES inbox_items(id),
                url           TEXT NOT NULL,
                final_url     TEXT,
                http_status   INTEGER,
                title         TEXT,
                summary       TEXT,
                fetched_at    TIMESTAMPTZ,
                fetch_error   TEXT
            )""",

        # Bodies are encrypted at field level (decision 09-01). Subject and sender stay
        # in the clear on purpose: they are what mail_search matches on.
        f"""CREATE TABLE IF NOT EXISTS mail_messages (
                id          SERIAL PRIMARY KEY,
                {_OWNED}
                gmail_id    TEXT NOT NULL,
                thread_id   TEXT,
                sender      TEXT,
                subject     TEXT,
                snippet     TEXT,
                body_enc    BYTEA,
                received_at TIMESTAMPTZ,
                fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, gmail_id)
            )""",

        f"""CREATE TABLE IF NOT EXISTS digests (
                id            SERIAL PRIMARY KEY,
                {_OWNED}
                kind          TEXT NOT NULL,
                for_date      DATE NOT NULL,
                payload       JSONB,
                tg_message_id BIGINT,
                sent_at       TIMESTAMPTZ,
                model         TEXT,
                cost_usd      NUMERIC(10, 6),
                UNIQUE (user_id, kind, for_date)
            )""",

        # A row, not an env var: adding an account is an INSERT, not a redeploy.
        f"""CREATE TABLE IF NOT EXISTS oauth_tokens (
                id              SERIAL PRIMARY KEY,
                {_OWNED}
                provider        TEXT NOT NULL,
                account_email   TEXT NOT NULL,
                refresh_token   BYTEA NOT NULL,
                scopes          TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_refresh_at TIMESTAMPTZ,
                last_error      TEXT,
                UNIQUE (user_id, provider, account_email)
            )""",

        # Declared now, filled in stage 8. No `vector` type and no CREATE EXTENSION:
        # turning pgvector on is its own decision with its own ticket.
        f"""CREATE TABLE IF NOT EXISTS embeddings (
                id          SERIAL PRIMARY KEY,
                {_OWNED}
                object_type TEXT NOT NULL,
                object_id   INTEGER NOT NULL,
                chunk_no    INTEGER NOT NULL DEFAULT 0,
                chunk_text  TEXT,
                model       TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, object_type, object_id, chunk_no)
            )""",

        # Every paid call with what it cost, per user (FR-38).
        f"""CREATE TABLE IF NOT EXISTS llm_call (
                id            SERIAL PRIMARY KEY,
                {_OWNED}
                inbox_item_id INTEGER REFERENCES inbox_items(id),
                purpose       TEXT NOT NULL,
                model         TEXT NOT NULL,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                cost_usd      NUMERIC(10, 6),
                latency_ms    INTEGER,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        "CREATE INDEX IF NOT EXISTS llm_call_user_day_idx ON llm_call (user_id, created_at)",

        # Work that must survive a redeploy. user_id is nullable here: a job can be
        # system-wide (an RSS poll for every user, a nightly dump).
        """CREATE TABLE IF NOT EXISTS job_queue (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
                kind         TEXT NOT NULL,
                payload      JSONB NOT NULL,
                run_after    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                attempts     INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                locked_by    TEXT,
                locked_at    TIMESTAMPTZ,
                status       TEXT NOT NULL DEFAULT 'pending',
                last_error   TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        """CREATE INDEX IF NOT EXISTS job_queue_ready_idx ON job_queue (run_after)
            WHERE status = 'pending'""",

        # Stage 1 additions, as ADD COLUMN so an existing database migrates on boot.
        # reply_text is what Svetochka answered (feeds the short history the agent
        # sees); suggestions are the FR-43 buttons, kept until one is tapped.
        "ALTER TABLE inbox_items ADD COLUMN IF NOT EXISTS reply_text TEXT",
        "ALTER TABLE inbox_items ADD COLUMN IF NOT EXISTS suggestions JSONB",
    ]


def init_db() -> None:
    """Run the schema. Called on every boot of the web service."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                for stmt in schema_statements():
                    cur.execute(stmt)
        logger.info("db: schema ready (%d statements)", len(schema_statements()))
    finally:
        conn.close()


def seed_users(chat_ids: list[str], tz: str) -> int:
    """Make sure every configured chat id has a users row. Returns how many were
    created. Idempotent: an existing row is left untouched, including its tz — a user
    who changed timezone in chat must not be reset by a redeploy."""
    if not chat_ids:
        return 0
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                created = 0
                for chat_id in chat_ids:
                    cur.execute(
                        """INSERT INTO users (telegram_chat_id, tz) VALUES (%s, %s)
                           ON CONFLICT (telegram_chat_id) DO NOTHING RETURNING id""",
                        (chat_id, tz),
                    )
                    if cur.fetchone():
                        created += 1
        if created:
            logger.info("db: seeded %d user(s)", created)
        return created
    finally:
        conn.close()


def ping() -> dict:
    """What /health reports about the database: reachable, and how many users exist."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM users")
                return {"ok": True, "users": int(cur.fetchone()["n"])}
    finally:
        conn.close()


# --- Users -------------------------------------------------------------------

def get_user_by_chat(chat_id) -> dict | None:
    """The access check (FR-2): no row, no user, no reply."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, telegram_chat_id, tz, status FROM users "
                            "WHERE telegram_chat_id = %s AND status = 'active'", (str(chat_id),))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


# --- Inbox -------------------------------------------------------------------

def claim_update(update_id: int | None, user_id: int, *, message_id=None, kind="text",
                 raw_text=None, file_id=None, duration_sec=None) -> int | None:
    """Record an incoming update and return its inbox_items.id — or None if this
    update_id was already seen. None is the caller's signal to stop, and it must be
    checked before anything paid happens (FR-3)."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO inbox_items
                       (user_id, tg_update_id, message_id, kind, raw_text, file_id, duration_sec)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (tg_update_id) DO NOTHING RETURNING id""",
                    (user_id, update_id, message_id, kind, raw_text, file_id, duration_sec),
                )
                row = cur.fetchone()
                return row["id"] if row else None
    finally:
        conn.close()


def mark_item(item_id: int, *, status: str, error: str | None = None,
              reply_text: str | None = None, suggestions: list | None = None) -> None:
    import json
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE inbox_items
                       SET status = %s, error = %s, processed_at = NOW(),
                           reply_text = COALESCE(%s, reply_text),
                           suggestions = COALESCE(%s::jsonb, suggestions)
                       WHERE id = %s""",
                    (status, error, reply_text,
                     json.dumps(suggestions, ensure_ascii=False) if suggestions is not None else None,
                     item_id),
                )
    finally:
        conn.close()


def get_item(user_id: int, item_id: int) -> dict | None:
    """Scoped by user on purpose: a callback payload is attacker-controlled text."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, raw_text, reply_text, suggestions FROM inbox_items "
                            "WHERE id = %s AND user_id = %s", (item_id, user_id))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


def recent_exchanges(user_id: int, limit: int = 8) -> list[dict]:
    """The short conversation history the agent sees: last N (message, reply) pairs,
    oldest first. Only text items with a reply — voice and failures add noise."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(raw_text, transcript) AS raw_text, reply_text FROM inbox_items
                       WHERE user_id = %s AND COALESCE(raw_text, transcript) IS NOT NULL
                         AND reply_text IS NOT NULL
                       ORDER BY id DESC LIMIT %s""",
                    (user_id, limit),
                )
                rows = [dict(r) for r in cur.fetchall()]
                return list(reversed(rows))
    finally:
        conn.close()


# --- Notes -------------------------------------------------------------------

def create_note(user_id: int, body: str, *, title: str | None = None,
                tags: list[str] | None = None, project: str | None = None,
                source: str = "telegram", source_ref: str | None = None,
                inbox_item_id: int | None = None) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO notes
                       (user_id, inbox_item_id, title, body, tags, project, source, source_ref)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (user_id, source, source_ref) DO UPDATE
                           SET updated_at = NOW(), deleted_at = NULL
                       RETURNING id""",
                    (user_id, inbox_item_id, title, body, tags or [], project, source, source_ref),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def search_notes(user_id: int, query: str, *, limit: int = 5,
                 source: str | None = None) -> list[dict]:
    """Russian full-text over title+body. plainto_tsquery, not to_tsquery: the input
    is a phrase typed on a phone, and to_tsquery raises on its punctuation."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, title, body, project, source, source_ref, created_at,
                               ts_rank(to_tsvector('russian', coalesce(title,'') || ' ' || body),
                                       plainto_tsquery('russian', %s)) AS rank
                        FROM notes
                        WHERE user_id = %s AND deleted_at IS NULL
                          {"AND source = %s" if source else ""}
                          AND to_tsvector('russian', coalesce(title,'') || ' ' || body)
                              @@ plainto_tsquery('russian', %s)
                        ORDER BY rank DESC, created_at DESC
                        LIMIT %s""",
                    (query, user_id, *([source] if source else []), query, limit),
                )
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def recent_notes(user_id: int, *, limit: int = 10, project: str | None = None) -> list[dict]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, title, body, project, source, created_at FROM notes
                        WHERE user_id = %s AND deleted_at IS NULL
                          {"AND project = %s" if project else ""}
                        ORDER BY created_at DESC LIMIT %s""",
                    (user_id, *([project] if project else []), limit),
                )
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def soft_delete_note(user_id: int, note_id: int) -> bool:
    """What the "Не туда" button does. Soft, because the text is rarely the mistake —
    the filing is — and it should survive being filed wrongly."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE notes SET deleted_at = NOW() "
                            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
                            (note_id, user_id))
                return cur.rowcount == 1
    finally:
        conn.close()


# --- Preferences and corrections ---------------------------------------------

def set_preference(user_id: int, key: str, value: str, *, set_via: str = "chat") -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO preferences (user_id, key, value, set_via)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (user_id, key) DO UPDATE
                           SET value = EXCLUDED.value, set_via = EXCLUDED.set_via, updated_at = NOW()""",
                    (user_id, key, value, set_via),
                )
    finally:
        conn.close()


def delete_preference(user_id: int, key: str) -> bool:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM preferences WHERE user_id = %s AND key = %s", (user_id, key))
                return cur.rowcount == 1
    finally:
        conn.close()


def list_preferences(user_id: int) -> dict[str, str]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM preferences WHERE user_id = %s ORDER BY key",
                            (user_id,))
                return {r["key"]: r["value"] for r in cur.fetchall()}
    finally:
        conn.close()


def add_correction(user_id: int, inbox_item_id: int | None, did: str,
                   should_have: str | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO corrections (user_id, inbox_item_id, did, should_have) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, inbox_item_id, did, should_have),
                )
    finally:
        conn.close()


def recent_corrections(user_id: int, limit: int = 5) -> list[dict]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT did, should_have, created_at FROM corrections "
                            "WHERE user_id = %s ORDER BY id DESC LIMIT %s", (user_id, limit))
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# --- Cost accounting ---------------------------------------------------------

def record_llm_call(user_id: int, inbox_item_id: int | None, purpose: str, model: str,
                    usage: dict, cost_usd: float, latency_ms: int) -> None:
    """Never raises: accounting must not be able to break what it accounts for."""
    try:
        conn = _conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO llm_call
                           (user_id, inbox_item_id, purpose, model, input_tokens, output_tokens,
                            cost_usd, latency_ms)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (user_id, inbox_item_id, purpose, model, usage.get("input_tokens"),
                         usage.get("output_tokens"), cost_usd, latency_ms),
                    )
        finally:
            conn.close()
    except Exception:
        logger.exception("db: failed to record llm_call (%s)", purpose)


def spend_today(user_id: int) -> float:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_call "
                            "WHERE user_id = %s AND created_at >= date_trunc('day', NOW())",
                            (user_id,))
                return float(cur.fetchone()["total"])
    finally:
        conn.close()


# --- Lists (FR-47…52) --------------------------------------------------------
# Names match case-insensitively on the trimmed text so "покупки", "Покупки" and
# "ПОКУПКИ " are one list. The stored name keeps the first spelling.

def find_list(user_id: int, name: str) -> dict | None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, kind FROM lists "
                            "WHERE user_id = %s AND lower(name) = lower(%s)",
                            (user_id, name.strip()))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


def get_or_create_list(user_id: int, name: str) -> dict:
    found = find_list(user_id, name)
    if found:
        return found
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO lists (user_id, name) VALUES (%s, %s) "
                            "ON CONFLICT (user_id, name) DO UPDATE SET name = EXCLUDED.name "
                            "RETURNING id, name, kind", (user_id, name.strip()))
                return dict(cur.fetchone())
    finally:
        conn.close()


def get_list(user_id: int, list_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, kind FROM lists WHERE id = %s AND user_id = %s",
                            (list_id, user_id))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


def list_lists(user_id: int) -> list[dict]:
    """Every list with its line counts, most recently touched first."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT l.id, l.name,
                              count(i.id) AS total,
                              count(i.id) FILTER (WHERE i.checked_at IS NULL) AS unchecked
                       FROM lists l LEFT JOIN list_items i ON i.list_id = l.id
                       WHERE l.user_id = %s
                       GROUP BY l.id ORDER BY max(i.created_at) DESC NULLS LAST, l.id""",
                    (user_id,))
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_list_items(user_id: int, list_id: int, texts: list[str]) -> list[int]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(position), 0) AS p FROM list_items "
                            "WHERE list_id = %s AND user_id = %s", (list_id, user_id))
                position = int(cur.fetchone()["p"])
                ids = []
                for text in texts:
                    position += 1
                    cur.execute("INSERT INTO list_items (user_id, list_id, text, position) "
                                "VALUES (%s, %s, %s, %s) RETURNING id",
                                (user_id, list_id, text, position))
                    ids.append(cur.fetchone()["id"])
                return ids
    finally:
        conn.close()


def list_items(user_id: int, list_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, list_id, text, position, checked_at FROM list_items "
                            "WHERE list_id = %s AND user_id = %s ORDER BY position, id",
                            (list_id, user_id))
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def set_list_item_checked(user_id: int, item_id: int, checked: bool | None) -> dict | None:
    """checked=None toggles. Returns the updated row (with list_id) or None if the
    line is not this user's."""
    if checked is None:
        expr = "CASE WHEN checked_at IS NULL THEN NOW() ELSE NULL END"
    elif checked:
        expr = "NOW()"
    else:
        expr = "NULL"
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE list_items SET checked_at = {expr}
                        WHERE id = %s AND user_id = %s
                        RETURNING id, list_id, text, position, checked_at""",
                    (item_id, user_id))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


def delete_list_item(user_id: int, item_id: int) -> bool:
    """The "Не туда" button for a list line. Hard delete: a line is a few words the
    user typed a minute ago, and moved_from history only matters for moves."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM list_items WHERE id = %s AND user_id = %s",
                            (item_id, user_id))
                return cur.rowcount == 1
    finally:
        conn.close()


def move_list_item(user_id: int, item_id: int, to_list_id: int) -> bool:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(position), 0) AS p FROM list_items "
                            "WHERE list_id = %s AND user_id = %s", (to_list_id, user_id))
                position = int(cur.fetchone()["p"]) + 1
                cur.execute(
                    """UPDATE list_items SET moved_from = list_id, list_id = %s, position = %s
                       WHERE id = %s AND user_id = %s AND list_id <> %s""",
                    (to_list_id, position, item_id, user_id, to_list_id))
                return cur.rowcount == 1
    finally:
        conn.close()


def unchecked_list_items(user_id: int, list_name: str) -> list[dict]:
    """What the brief and the evening review read (FR-50)."""
    found = find_list(user_id, list_name)
    if not found:
        return []
    return [i for i in list_items(user_id, found["id"]) if i["checked_at"] is None]


# --- Reminders (FR-19…23) ----------------------------------------------------

def create_reminder(user_id: int, text: str, fire_at, tz: str, dedup_key: str,
                    inbox_item_id: int | None = None) -> tuple[int, bool]:
    """Returns (id, created). created=False means the same reminder already exists
    (FR-22) and the id is the existing row's."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO reminders (user_id, inbox_item_id, text, fire_at, tz, dedup_key)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (user_id, dedup_key) DO NOTHING RETURNING id""",
                    (user_id, inbox_item_id, text, fire_at, tz, dedup_key))
                row = cur.fetchone()
                if row:
                    return row["id"], True
                cur.execute("SELECT id FROM reminders WHERE user_id = %s AND dedup_key = %s",
                            (user_id, dedup_key))
                return cur.fetchone()["id"], False
    finally:
        conn.close()


def list_reminders(user_id: int, *, status: str = "scheduled", limit: int = 20) -> list[dict]:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, text, fire_at, tz, status FROM reminders "
                            "WHERE user_id = %s AND status = %s ORDER BY fire_at LIMIT %s",
                            (user_id, status, limit))
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_reminder(user_id: int, reminder_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, text, fire_at, tz, status FROM reminders "
                            "WHERE id = %s AND user_id = %s", (reminder_id, user_id))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()


def set_reminder_status(user_id: int, reminder_id: int, status: str, fire_at=None) -> bool:
    """done / cancelled, or scheduled again with a new fire_at (snooze, tomorrow).
    The dedup_key stays, so the snoozed reminder still counts as the same one."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE reminders
                       SET status = %s, fire_at = COALESCE(%s, fire_at),
                           sent_at = CASE WHEN %s = 'scheduled' THEN NULL ELSE sent_at END
                       WHERE id = %s AND user_id = %s""",
                    (status, fire_at, status, reminder_id, user_id))
                return cur.rowcount == 1
    finally:
        conn.close()


def deliver_due_reminders(deliver, *, limit: int = 20) -> int:
    """The tick's only query (§7). Locks due rows with SKIP LOCKED so two web
    replicas never send the same reminder; calls deliver(row) for each while the
    lock is held and marks 'sent' only when it returned True — a Telegram outage
    leaves the row scheduled for the next tick. Returns how many were sent."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.id, r.user_id, r.text, r.fire_at, r.tz, u.telegram_chat_id
                       FROM reminders r JOIN users u ON u.id = r.user_id
                       WHERE r.status = 'scheduled' AND r.fire_at <= NOW()
                       ORDER BY r.fire_at LIMIT %s
                       FOR UPDATE OF r SKIP LOCKED""",
                    (limit,))
                rows = [dict(r) for r in cur.fetchall()]
                sent = 0
                for row in rows:
                    if deliver(row):
                        cur.execute("UPDATE reminders SET status = 'sent', sent_at = NOW() WHERE id = %s",
                                    (row["id"],))
                        sent += 1
                return sent
    finally:
        conn.close()


# --- Links (FR-11, FR-12) ----------------------------------------------------

def create_link(user_id: int, url: str, *, inbox_item_id: int | None = None,
                final_url: str | None = None, http_status: int | None = None,
                title: str | None = None, summary: str | None = None,
                fetch_error: str | None = None) -> int:
    """Every fetch attempt leaves a row, successful or not (FR-11: a 404 is
    recorded)."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO links (user_id, inbox_item_id, url, final_url, http_status,
                                          title, summary, fetched_at, fetch_error)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s) RETURNING id""",
                    (user_id, inbox_item_id, url, final_url, http_status, title, summary, fetch_error))
                return cur.fetchone()["id"]
    finally:
        conn.close()


# --- Voice (FR-16) -----------------------------------------------------------

def set_transcript(item_id: int, transcript: str) -> None:
    """The transcript is the item's text from here on: raw_text stays NULL (the raw
    thing was audio), and recent_exchanges reads COALESCE(raw_text, transcript)."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE inbox_items SET transcript = %s WHERE id = %s",
                            (transcript, item_id))
    finally:
        conn.close()
