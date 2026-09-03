"""Env has to be set before sveta.core.config is imported — it reads os.environ at
import time. pytest loads conftest first, which is the whole reason this file exists."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("SVETA_TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("SVETA_ALLOWED_CHAT_IDS", "111,222")
os.environ.setdefault("SVETA_WEBHOOK_SECRET", "s3cret")
os.environ.setdefault("SVETA_TOKEN_KEY", "hqlDKz3nEXlPPYxLZ0FR6oy4jZmyMnAcQBpu2AsIkFo=")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
