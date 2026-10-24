"""Atomic, idempotent Upstash history with a 10 MB dialogue payload budget.

Capacity is shared by all actors/channels in this database namespace. Reject
new writes at the limit; never silently delete history. No automatic expiry.
"""

import json

import httpx

from prbot.config import ConfigError

MAX_BYTES = 10_000_000
# One atomic Lua script prevents concurrent desktop/Telegram writes exceeding budget.
SAVE = """
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old then return 0 end
local used = tonumber(redis.call('GET', KEYS[2]) or '0')
local size = string.len(ARGV[1]) + string.len(ARGV[2])
if used + size > tonumber(ARGV[3]) then return -1 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('SET', KEYS[2], used + size)
return 1
"""


class ConversationStore:
    def __init__(self, values):
        self.url = values["UPSTASH_REDIS_REST_URL"].rstrip("/")
        self.token = values["UPSTASH_REDIS_REST_TOKEN"]
        if not self.url.startswith("https://"):
            raise ConfigError("UPSTASH_REDIS_REST_URL must use HTTPS.")

    async def command(self, *command):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(
                    self.url,
                    json=list(command),
                    headers={
                        "Authorization": "Bearer " + self.token,
                    },
                )
                response.raise_for_status()
                body = response.json()
                if body.get("error"):
                    raise ValueError
                return body["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ConfigError("Message not saved. Retry.") from None

    async def save(self, message):
        field = message["conversation_id"] + ":" + message["message_id"]
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        result = await self.command(
            "EVAL", SAVE, 2, "prbot:history:v1", "prbot:history:bytes:v1", field, encoded, MAX_BYTES
        )
        if result == -1:
            raise ConfigError("Message not saved. Retry. The 10 MB history limit is reached.")

    async def history(self, conversation_id, cursor="0", count=50):
        # HSCAN is a cursor page; ordering/dedup is applied by the client across pages.
        result = await self.command(
            "HSCAN",
            "prbot:history:v1",
            str(cursor),
            "MATCH",
            conversation_id + ":*",
            "COUNT",
            min(count, 100),
        )
        try:
            messages = [json.loads(value) for value in result[1][1::2]]
            return {
                "cursor": str(result[0]),
                "messages": sorted(
                    messages,
                    key=lambda msg: (
                        msg["sequence"],
                        msg.get("logical_order", 0),
                        msg["message_id"],
                    ),
                ),
            }
        except (ValueError, KeyError, TypeError, IndexError):
            raise ConfigError("History unavailable. Retry loading messages.") from None

    async def messages(self):
        """Read the bounded legacy hash, dedup, then sort before display pagination.

        HSCAN cursors are storage cursors, not chronological pages. v1 records
        remain readable; no destructive migration or expiry is required.
        """
        cursor = "0"
        messages = {}
        seen = set()
        try:
            while True:
                page = await self.command("HSCAN", "prbot:history:v1", cursor, "COUNT", 1000)
                cursor = str(page[0])
                for field, encoded in zip(page[1][::2], page[1][1::2]):
                    message = json.loads(encoded)
                    if not isinstance(message, dict):
                        raise ValueError
                    messages[field] = message
                if cursor == "0":
                    break
                if cursor in seen:
                    raise ValueError
                seen.add(cursor)
            return sorted(
                messages.values(),
                key=lambda m: (m["sequence"], m.get("logical_order", 0), m["message_id"]),
            )
        except (ValueError, KeyError, TypeError, IndexError):
            raise ConfigError("History unavailable. Retry loading messages.") from None
