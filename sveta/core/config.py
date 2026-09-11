"""Settings, read once from the environment.

Two rules, both from SPEC.md:

- Secrets live only in the Railway environment. Nothing here has a default that
  would let the service boot with a made-up credential.
- A missing required variable fails startup loudly (§9, §11 stage 0 DoD). A bot that
  boots without a webhook secret and answers 503 to every update looks, from the
  phone, identical to a bot that was never deployed.
"""
import os
from pathlib import Path

if not os.environ.get("SVETA_SKIP_DOTENV"):
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass  # not installed on Railway, where variables are set directly


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among `names`.

    The first name is canonical (SPEC.md §12). The rest are aliases Dimitry used when
    entering the variables on 2026-09-03; accepting them costs nothing and avoids a
    redeploy to rename. New code and docs use the canonical names only.
    """
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return default


# --- Transport ---------------------------------------------------------------

TELEGRAM_TOKEN = _env("SVETA_TELEGRAM_TOKEN", "sveta_telegram_token")

# Seeds the first row of `users` on first start (SPEC.md FR-2, §7.3). It is NOT the
# access boundary — `users` is. A list from day one: the second user is an INSERT.
ALLOWED_CHAT_IDS: list[str] = [
    c.strip() for c in _env("SVETA_ALLOWED_CHAT_IDS").split(",") if c.strip()
]

# Shared secret in the webhook path AND in Telegram's X-Telegram-Bot-Api-Secret-Token
# header (SPEC.md §6.5). Unset = the route answers 503; it never falls open.
WEBHOOK_SECRET = _env("SVETA_WEBHOOK_SECRET")

# --- Storage -----------------------------------------------------------------

DATABASE_URL = _env("DATABASE_URL")

# Fernet key for OAuth refresh tokens and raw mail bodies (SPEC.md §8). Lives only in
# the environment; a database dump without it is inert.
TOKEN_KEY = _env("SVETA_TOKEN_KEY")

# --- Models ------------------------------------------------------------------

ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "sveta_anthropic")
# Only for an *identity-linked* Anthropic key (the kind the Console issues to a
# person rather than to a workspace): the API then requires the workspace id on
# every request — first live call, 2026-09-03: "anthropic-workspace-id is required
# when authenticating with an identity-linked API key". A workspace-scoped key
# needs nothing here. Both work; the choice is Dimitry's.
ANTHROPIC_WORKSPACE_ID = _env("ANTHROPIC_WORKSPACE_ID", "SVETA_ANTHROPIC_WORKSPACE_ID")
OPENAI_API_KEY = _env("OPENAI_API_KEY", "sveta_openai_api")

# IDs without date suffixes (SPEC.md §6.1). effort is sent to the agent model only.
AGENT_MODEL = _env("SVETA_AGENT_MODEL", default="claude-sonnet-5")
CHEAP_MODEL = _env("SVETA_CHEAP_MODEL", default="claude-haiku-4-5")
BRIEF_MODEL = _env("SVETA_BRIEF_MODEL", default=AGENT_MODEL)
TRANSCRIBE_MODEL = _env("SVETA_TRANSCRIBE_MODEL", default="whisper-1")

# Per-user daily ceiling, checked against llm_call before each paid call (FR-38).
DAILY_USD_LIMIT = float(_env("SVETA_DAILY_USD_LIMIT", default="1.50"))

# --- Behaviour ---------------------------------------------------------------

# The first user's timezone; later users carry their own in users.tz.
TZ = _env("SVETA_TZ", default="Asia/Jerusalem")

MAX_VOICE_SECONDS = int(_env("SVETA_MAX_VOICE_SECONDS", default="600"))
MAX_MESSAGE_CHARS = 4000

# The reminder tick period inside the web process (FR-20: delivery within a
# minute). 0 disables the thread — tests, and any role that must not send.
TICK_SECONDS = int(_env("SVETA_TICK_SECONDS", default="20"))

# --- Startup -----------------------------------------------------------------

# (canonical name, accepted aliases). What the deployed stages need: the OpenAI
# key since stage 2 (voice); Google and Notion arrive with stages 3 and 5.
REQUIRED: tuple[tuple[str, ...], ...] = (
    ("SVETA_TELEGRAM_TOKEN", "sveta_telegram_token"),
    ("SVETA_ALLOWED_CHAT_IDS",),
    ("SVETA_WEBHOOK_SECRET",),
    ("SVETA_TOKEN_KEY",),
    ("DATABASE_URL",),
    ("ANTHROPIC_API_KEY", "sveta_anthropic"),
    ("OPENAI_API_KEY", "sveta_openai_api"),
)


def validate_secrets() -> None:
    """Fail fast, and say exactly which variable is missing by its canonical name."""
    missing = [names[0] for names in REQUIRED if not _env(*names)]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
