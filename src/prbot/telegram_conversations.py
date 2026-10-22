"""Telegram adapter for the same service used by the desktop window."""

import time

from prbot.config import ConfigError
from prbot.conversation_service import target_link


class TelegramConversations:
    def __init__(self, application):
        self.application = application

    def scope(self, update):
        message = update.message
        actor = str(message.from_user.id) if message.from_user else "unknown"
        return f"{message.chat.id}:{actor}:{message.message_thread_id or 0}"

    async def archive_update(self, update, reply=None):
        message = update.message
        if not message or not message.from_user:
            return
        scope = self.scope(update)
        with self.application.db() as db:
            row = db.execute("SELECT mode FROM channel_sessions WHERE scope=?", (scope,)).fetchone()
        mode = row["mode"] if row else "Issue"
        if "/pull/" in (message.text or ""):
            mode = "Pull Request"
        existing = self.application.request(f"telegram:{update.update_id}")
        if existing:
            mode = existing["mode"]
        cid = self.application.conversation(scope, "telegram", mode)
        now = time.time()
        await self.application.archive.save(
            {
                "conversation_id": cid,
                "message_id": f"telegram:{update.update_id}:" + ("reply" if reply else "user"),
                "source": "telegram",
                "actor_id": scope,
                "role": "assistant" if reply else "user",
                "content": self.application.clean(reply if reply else message.text or ""),
                "created_at": now,
                "delivery_status": "saved",
                "task_id": f"telegram:{update.update_id}",
                "sequence": now,
            }
        )

    async def handle_async(self, update, command=None):
        message = update.message
        if not message.from_user:
            return None
        actor = str(message.from_user.id)
        # A bound operator is required; Telegram senders never inherit local desktop trust.
        bound = str(self.application.values.get("PRBOT_TELEGRAM_ACTOR_ID", ""))
        if actor != bound:
            return (
                (
                    "Telegram tasks are not bound to this user. Add PRBOT_TELEGRAM_ACTOR_ID "
                    "in Infisical using the User ID shown by /id."
                )
                if command
                else None
            )
        scope = f"{message.chat.id}:{actor}:{message.message_thread_id or 0}"
        with self.application.db() as db:
            session = db.execute(
                "SELECT link,mode,expires FROM channel_sessions WHERE scope=?", (scope,)
            ).fetchone()
            if command == "cancel":
                db.execute("DELETE FROM channel_sessions WHERE scope=?", (scope,))
                return "Input cancelled. Accepted tasks continue in the background."
            if command == "finishissue":
                db.execute(
                    "INSERT OR REPLACE INTO channel_sessions VALUES (?,?,?,?)",
                    (scope, None, "Issue", time.time() + 900),
                )
                return "Send a GitHub repository, issue or pull request link."
        if not session:
            return None
        if session["expires"] < time.time():
            return "Input expired. Send /FinishIssue again."
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        link, mode = session["link"], session["mode"]
        if not link:
            link = parts[0]
            mode = "Pull Request" if "/pull/" in link else "Issue"
            try:
                target_link(link, mode)
            except ConfigError as error:
                return str(error)
            if len(parts) == 1:
                with self.application.db() as db:
                    db.execute(
                        "UPDATE channel_sessions SET link=?,mode=? WHERE scope=?",
                        (link, mode, scope),
                    )
                return "Describe the change, expected outcome and acceptance criteria."
            text = parts[1]
        try:
            task = await self.application.submit(
                f"telegram:{update.update_id}", scope, "telegram", mode, link, text
            )
        except ConfigError as error:
            return str(error)
        with self.application.db() as db:
            db.execute("DELETE FROM channel_sessions WHERE scope=?", (scope,))
        return f"Task {task['id']} accepted: {task['status']}."


async def send_results(application, bot, stop):
    import asyncio

    from telegram.error import BadRequest, Forbidden, TelegramError

    with application.db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS delivered_results "
            "(id TEXT PRIMARY KEY, status TEXT, attempts INTEGER)"
        )
    while not stop.is_set():
        with application.db() as db:
            rows = db.execute(
                "SELECT r.* FROM requests r LEFT JOIN delivered_results d "
                "ON r.id=d.id WHERE r.source='telegram' AND r.status IN "
                "('completed','failed','interrupted','save_pending','needs_clarification') AND "
                "(d.id IS NULL OR (d.status='pending' AND d.attempts<5))"
            ).fetchall()
        for row in rows:
            # A save_pending result can be delivered but still needs archive-only retry.
            text = f"Task {row['id']}: {row['status']}\n{row['result'] or ''}"
            chat, _, thread = row["actor"].split(":")
            status = "pending"
            try:
                await bot.send_message(
                    chat_id=int(chat), message_thread_id=int(thread) or None, text=text[:4000]
                )
                status = "delivered"
            except (BadRequest, Forbidden):
                status = "rejected"
            except TelegramError:
                pass
            with application.db() as db:
                db.execute(
                    "INSERT INTO delivered_results VALUES (?,?,1) ON CONFLICT(id) "
                    "DO UPDATE SET status=excluded.status,attempts=attempts+1",
                    (row["id"], status),
                )
        try:
            await asyncio.wait_for(stop.wait(), 3)
        except TimeoutError:
            pass
