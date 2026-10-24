"""Remote-only real Redis/SDK proof through Telegram's update adapter.

Updates are local fixtures; no real chat receives an acceptance message.
"""

import json
import tempfile
import time
import uuid
from pathlib import Path

from telegram import Update

from prbot.acceptance_interaction import require
from prbot.codex_adapter import CodexAdapter
from prbot.conversation_service import ConversationService
from prbot.execution_setup import codex_login
from prbot.telegram_conversations import TelegramConversations


async def check_telegram(values, archive, fields, evidence):
    uid = int(time.time() * 1000) % 1000000000
    actor = 900000000000 + uid
    scope = f"{actor}:{actor}:0"
    tid = f"telegram:{uid}"
    marker = uuid.uuid4().hex
    settings = dict(values, PRBOT_TELEGRAM_ACTOR_ID=str(actor))
    native = CodexAdapter(codex_login())
    with tempfile.TemporaryDirectory(prefix="prbot-telegram-evidence-") as directory:
        output = Path(directory) / "decision.txt"

        class TelegramAdapter:
            calls = 0

            async def invoke(self, path, prompt, schema, timeout):
                self.calls += 1
                continuation = json.loads(prompt)["continuation"]
                instruction = (
                    "Verification only. Do not modify repositories, publish anything, "
                    "run tests/builds, send messages or read secrets. Return the supplied schema "
                    "with empty pr_url. "
                )
                if not continuation["answers"]:
                    instruction += (
                        "Ask which marker to write. Return needs_clarification. Do not write files."
                    )
                else:
                    instruction += (
                        f"Write the exact marker from the last user's answer to {output}, "
                        "then read it back. Return completed only after verifying the content. "
                        "Original task context: " + json.dumps(continuation)
                    )
                return await native.invoke(path, instruction, schema, min(timeout, 240))

        adapter = TelegramAdapter()
        application = ConversationService(archive=archive, adapter=adapter, values=settings)
        conversation = application.conversation(scope, "telegram", "Issue")
        for update_id in (uid, uid + 1, uid + 2, uid + 3):
            fields.extend(
                f"{conversation}:telegram:{update_id}:{role}" for role in ("user", "reply")
            )
        fields.extend(f"{conversation}:{tid}:assistant{suffix}" for suffix in ("", ":1"))

        def update(text, update_id):
            return Update.de_json(
                {
                    "update_id": update_id,
                    "message": {
                        "message_id": update_id,
                        "date": int(time.time()),
                        "chat": {"id": actor, "type": "private"},
                        "from": {"id": actor, "is_bot": False, "first_name": "Acceptance"},
                        "text": text,
                    },
                },
                None,
            )

        try:
            flow = TelegramConversations(application)
            await flow.handle_async(update("/FinishIssue", uid), "finishissue")
            await flow.handle_async(update("https://github.com/C0verSnow/PRBot/issues/13", uid + 1))
            await flow.handle_async(
                update("Ask which marker to write, then wait for my answer.", uid + 2)
            )
            await application.execute(application.request(tid))
            require(
                application.request(tid)["status"] == "needs_clarification",
                "Telegram SDK task did not request clarification.",
            )
            require(not output.exists(), "SDK acted before the Telegram answer.")
            application = ConversationService(archive=archive, adapter=adapter, values=settings)
            flow = TelegramConversations(application)
            answer = update(f"/answer {tid} {marker}", uid + 3)
            await flow.handle_async(answer, "answer")
            await flow.handle_async(answer, "answer")
            await application.execute(application.request(tid))
            require(
                application.request(tid)["status"] == "completed",
                "Telegram SDK continuation did not complete.",
            )
            require(output.read_text().strip() == marker, "Telegram answer did not reach real SDK.")
            require(adapter.calls == 2, "Duplicate Telegram answer repeated SDK execution.")
            page = await application.desktop_history("local:acceptance", "Issue")
            messages = [m for m in page["messages"] if m.get("task_id") == tid]
            # Fixtures may share the Redis database with real history; follow all display pages.
            cursor = page["cursor"]
            while cursor != "0":
                page = await application.desktop_history("local:acceptance", "Issue", cursor)
                messages.extend(m for m in page["messages"] if m.get("task_id") == tid)
                cursor = page["cursor"]
            require(len(messages) == 10, "Telegram creation/clarification archive is incomplete.")
            require(
                all(m["actor_id"] == scope for m in messages), "Telegram history scope mismatch."
            )
            evidence["real_telegram_adapter_same_task_sdk_answer"] = True
            evidence["real_telegram_duplicate_answer_idempotent"] = True
            evidence["real_telegram_history_in_desktop"] = True
        finally:
            with application.db() as db:
                for table, column in (
                    ("requests", "id"),
                    ("task_events", "task_id"),
                    ("replies", "task_id"),
                    ("pending_answers", "task_id"),
                    ("telegram_notices", "task_id"),
                    ("telegram_updates", "task_id"),
                    ("telegram_drafts", "task_id"),
                ):
                    db.execute(f"DELETE FROM {table} WHERE {column}=?", (tid,))
                db.execute("DELETE FROM channel_sessions WHERE scope=?", (scope,))
