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
