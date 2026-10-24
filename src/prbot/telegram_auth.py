"""Bounded runtime authorization. Never return stale grants after refresh failure."""

import asyncio
import re
import time

from prbot.secrets import load_secrets


def actor_ids(value):
    """Single numeric ID or comma/whitespace separated IDs; invalid lists deny all."""
    if not isinstance(value, str) or not re.fullmatch(r"\s*\d+(?:[\s,]+\d+)*\s*", value):
        return set()
    return set(re.split(r"[\s,]+", value.strip()))


class TelegramAuthorization:
    TTL = 60
    REFRESH_INTERVAL = 5

    def __init__(self, application):
        self.application = application
        self.expires = time.monotonic() + self.TTL
        self.last_refresh = float("-inf")
        self.lock = asyncio.Lock()

    async def allows(self, actor):
        async with self.lock:
            now = time.monotonic()
            known = actor_ids(self.application.values.get("PRBOT_TELEGRAM_ACTOR_ID", ""))
            if actor in known and now < self.expires:
                return True
            if now - self.last_refresh < self.REFRESH_INTERVAL:
                return False
            self.last_refresh = now
            try:
                values = await asyncio.to_thread(load_secrets, refresh=True, timeout=5)
                value = values.get("PRBOT_TELEGRAM_ACTOR_ID", "")
            except Exception:
                value = ""  # Fail closed without echoing provider errors or credentials.
            self.application.values["PRBOT_TELEGRAM_ACTOR_ID"] = value
            self.expires = time.monotonic() + self.TTL
            self.last_refresh = time.monotonic()
            return actor in actor_ids(value)
