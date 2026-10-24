"""Atomic, idempotent Upstash history with a 10 MB dialogue payload budget.

Capacity is shared by all actors/channels in this database namespace. Reject
new writes at the limit; never silently delete history. No automatic expiry.
"""

import hashlib
import json

import httpx

from prbot.config import ConfigError
from prbot.journal import initialize_journal, journal_db

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
        initialize_journal()
        # Rotate credentials without losing history; separate different Redis databases.
        self.scope = hashlib.sha256(self.url.encode()).hexdigest()
        with journal_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO history_settings VALUES ('last_scope', ?)",
                (self.scope,),
            )

    def cache_messages(self, messages):
        with journal_db() as db:
            db.executemany(
                "INSERT OR REPLACE INTO history_messages VALUES (?,?,?,?)",
                [(self.scope, m.get("conversation_id", ""), m["message_id"],
                  json.dumps(m, ensure_ascii=False)) for m in messages],
            )

    def local_messages(self):
        with journal_db() as db:
            messages = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM history_messages WHERE scope=?", (self.scope,)
            )]
        return sorted(messages, key=lambda m: (
            m["sequence"], m.get("logical_order", 0), m["message_id"]
        ))

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
        if result not in (0, 1):
            raise ConfigError("Message not saved. Retry.")
        if result == 0:
            # A retry may follow a process exit after Redis committed but before
            # SQLite did. Cache the authoritative value, never the retry payload.
            try:
                stored = json.loads(await self.command("HGET", "prbot:history:v1", field))
                if (stored["conversation_id"], stored["message_id"]) != (
                    message["conversation_id"], message["message_id"]
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise ConfigError("Message not saved locally. Retry.") from None
            self.cache_messages([stored])
        else:
            self.cache_messages([message])

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
            ordered = sorted(
                messages.values(),
                key=lambda m: (m["sequence"], m.get("logical_order", 0), m["message_id"]),
            )
            self.cache_messages(ordered)
            return ordered
        except (ValueError, KeyError, TypeError, IndexError):
            raise ConfigError("History unavailable. Retry loading messages.") from None


class LocalHistoryArchive(ConversationStore):
    """Last synced local desktop data when cloud bootstrap cannot be loaded.

    No cached Telegram grants are reused. ConversationService applies actor and
    source filtering with an empty authorization set before returning records.
    """

    def __init__(self):
        initialize_journal()
        with journal_db() as db:
            row = db.execute(
                "SELECT value FROM history_settings WHERE key='last_scope'"
            ).fetchone()
        self.scope = row[0] if row else ""

    async def messages(self):
        raise ConfigError("Connections unavailable. Showing local history.")

    async def save(self, message):
        raise ConfigError("Message not saved. Restore connections and retry.")
