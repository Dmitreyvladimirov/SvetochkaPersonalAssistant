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

# Stage 3. Google: an OAuth "Web application" client whose redirect URI is
# https://<PUBLIC_DOMAIN>/oauth/google/callback; the refresh token per user lives
# in oauth_tokens, encrypted. Notion: one internal integration token (v1); the
# target database is a per-user preference set with /notion. All optional: the
# bot works without them and says so.
GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
NOTION_TOKEN = _env("NOTION_TOKEN")                 # fallback: one internal integration
NOTION_CLIENT_ID = _env("NOTION_CLIENT_ID")         # preferred: a public integration, OAuth per user
NOTION_CLIENT_SECRET = _env("NOTION_CLIENT_SECRET")
PUBLIC_DOMAIN = _env("RAILWAY_PUBLIC_DOMAIN", "SVETA_PUBLIC_DOMAIN")

# Which model house answers (SPEC §7.1). The tools, playbooks and agent loop are
# provider-neutral; only sveta/core/providers/<name>.py is not. Switching is this
# variable, and the golden set can be run against both to compare on our own
# scenarios rather than on benchmarks.
PROVIDER = _env("SVETA_PROVIDER", default="openai").lower()


def _model(role: str, env_name: str) -> str:
    """The model for one role: the environment wins, otherwise the provider's own
    default. Roles, not tiers, because the cheap ones differ by job — reading an
    attachment is not the same work as planning a Gmail query."""
    from sveta.core.providers import get
    chosen = _env(env_name)
    return chosen or get(PROVIDER).DEFAULT_MODELS[role]


# IDs without date suffixes (SPEC.md §6.1). effort is sent to the agent model only.
AGENT_MODEL = _model("agent", "SVETA_AGENT_MODEL")
CHEAP_MODEL = _model("cheap", "SVETA_CHEAP_MODEL")
# Attachments are read by the cheapest model that actually reads them: a ticket
# PDF came back complete from the cheap tier of both providers (tested
# 2026-09-12), and this is the highest-volume paid call Svetochka makes.
VISION_MODEL = _model("vision", "SVETA_VISION_MODEL")
# Turning "билеты в Колумбию" into Gmail queries is judgement, not extraction:
# it has to know that Colombia means Bogota and BOG (FR-62).
PLAN_MODEL = _model("plan", "SVETA_PLAN_MODEL")
BRIEF_MODEL = _model("brief", "SVETA_BRIEF_MODEL")
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
# The key for whichever provider actually answers. The other one is optional: a
# deployment on OpenAI must not fail startup over a missing Anthropic key.
AGENT_KEY_NAMES: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "sveta_anthropic"),
    "openai": ("OPENAI_API_KEY", "sveta_openai_api"),
}


def provider_api_key() -> str:
    return _env(*AGENT_KEY_NAMES.get(PROVIDER, AGENT_KEY_NAMES["openai"]))


_AGENT_KEY = AGENT_KEY_NAMES.get(PROVIDER, AGENT_KEY_NAMES["openai"])

REQUIRED: tuple[tuple[str, ...], ...] = (
    ("SVETA_TELEGRAM_TOKEN", "sveta_telegram_token"),
    ("SVETA_ALLOWED_CHAT_IDS",),
    ("SVETA_WEBHOOK_SECRET",),
    ("SVETA_TOKEN_KEY",),
    ("DATABASE_URL",),
    _AGENT_KEY,
    ("OPENAI_API_KEY", "sveta_openai_api"),      # Whisper, whoever runs the agent
)


# The cron roles need less: no webhook, no seed list, no Whisper.
REQUIRED_BY_ROLE: dict[str, tuple[tuple[str, ...], ...]] = {
    "web": REQUIRED,
    "digest": (("DATABASE_URL",), ("SVETA_TELEGRAM_TOKEN", "sveta_telegram_token"), _AGENT_KEY),
    "ingest": (("DATABASE_URL",),),
}


def validate_secrets(role: str = "web") -> None:
    """Fail fast, and say exactly which variable is missing by its canonical name.

    Which key is required moves with SVETA_PROVIDER, so the message says so: the
    `digest` cron crashed on 2026-09-12 with a bare "Missing required env vars:
    OPENAI_API_KEY" after the provider switch, and nothing in it explained that
    the requirement had changed under the service rather than the variable being
    lost."""
    wanted = dict.fromkeys(REQUIRED_BY_ROLE.get(role, REQUIRED))   # the same key twice is one
    missing = [names[0] for names in wanted if not _env(*names)]
    if not missing:
        return
    why = ""
    if _AGENT_KEY[0] in missing:
        why = (f" — {_AGENT_KEY[0]} is what SVETA_PROVIDER={PROVIDER!r} needs; set it on this "
               f"service, or set SVETA_PROVIDER to the provider whose key it already has")
    raise EnvironmentError(f"Missing required env vars ({role}): {', '.join(missing)}{why}")
