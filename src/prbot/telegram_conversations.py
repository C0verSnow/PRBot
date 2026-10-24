"""Telegram adapter with durable update routing and original-task continuation."""

import json
import re
import time

from prbot.config import ConfigError
from prbot.conversation_service import target_link
from prbot.telegram_auth import TelegramAuthorization


class TelegramConversations:
    def __init__(self, application):
        self.application = application
        self.authorization = TelegramAuthorization(application)

    def scope(self, update):
        message = update.message
        actor = str(message.from_user.id) if message.from_user else "unknown"
        return f"{message.chat.id}:{actor}:{message.message_thread_id or 0}"

    async def archive_update(self, update, reply=None):
        # The handler first persists a validated route. Receiver's pre-dispatch
        # hook is a no-op for new updates, and retries use the exact saved route.
        with self.application.db() as db:
            route = db.execute(
                "SELECT * FROM telegram_updates WHERE id=?", (update.update_id,)
            ).fetchone()
        if not route:
            message = update.message
            if (
                reply
                and message
                and message.from_user
                and message.chat.type in {"private", "group", "supergroup"}
                and await self.authorization.allows(str(message.from_user.id))
            ):
                await self.record_response(update, reply)
            return
        cid = self.application.conversation(route["scope"], "telegram", route["mode"])
        await self.application.archive.save(
            {
                "conversation_id": cid,
                "message_id": f"telegram:{update.update_id}:" + ("reply" if reply else "user"),
                "source": "telegram",
                "actor_id": route["scope"],
                "role": "assistant" if reply else "user",
                "content": self.application.clean(reply if reply else route["content"]),
                "created_at": route["created"],
                "delivery_status": "saved",
                "task_id": route["task_id"],
                "mode": route["mode"],
                "logical_order": update.update_id * 2 + (1 if reply else 0),
                # Telegram event time and update ID establish order independent of
                # retries/arrival. The reply occupies the next logical half-step.
                "sequence": route["created"] + (0.000001 if reply else 0),
            }
        )

    async def record_response(self, update, response):
        """Keep selection/error/utility replies without assigning an unrelated task."""
        scope = self.scope(update)
        message = update.message
        date = getattr(message, "date", None)
        created = (date.timestamp() if date else time.time()) + update.update_id / 1000000000000
        with self.application.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO telegram_updates VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    update.update_id,
                    scope,
                    "",
                    "Issue",
                    json.dumps({"response": response}),
                    self.application.clean(message.text or ""),
                    created,
                ),
            )
        await self.archive_update(update)
        await self.archive_update(update, response)
        with self.application.db() as db:
            db.execute(
                "UPDATE telegram_updates SET reply=? WHERE id=?", (response, update.update_id)
            )
        return response

    async def handle_async(self, update, command=None):
        message = update.message
        if not message or not message.from_user:
            return None
        actor = str(message.from_user.id)
        if not await self.authorization.allows(actor):
            return (
                (
                    "Telegram tasks are not bound to this user. Add PRBOT_TELEGRAM_ACTOR_ID "
                    "in Infisical using the User ID shown by /id."
                )
                if command
                else None
            )
        scope = self.scope(update)
        text = (message.text or "").strip()
        with self.application.db() as db:
            route = db.execute(
                "SELECT * FROM telegram_updates WHERE id=?", (update.update_id,)
            ).fetchone()
            if route:
                if route["scope"] != scope:
                    raise ConfigError("Message belongs to another conversation.")
                if route["reply"] is not None:
                    return route["reply"]
                snapshot = json.loads(route["snapshot"])
            else:
                row = db.execute(
                    "SELECT * FROM channel_sessions WHERE scope=?", (scope,)
                ).fetchone()
                draft = db.execute("SELECT task_id FROM telegram_drafts WHERE scope=?", (scope,))
                draft = draft.fetchone()
                snapshot = {
                    "session": dict(row) if row else None,
                    "task_id": draft["task_id"] if draft else None,
                }
                # Explicit /answer task-id text or replying to a notification has
                # priority over task creation and ambiguous implicit matching.
                explicit = re.match(r"^/answer(?:@\w+)?\s+([\w:-]+)\s+(.+)$", text, re.S)
                reply_to = getattr(message, "reply_to_message", None)
                selected = None
                if explicit:
                    selected = explicit[1]
                    snapshot["answer"] = explicit[2]
                elif reply_to:
                    notice = db.execute(
                        "SELECT n.task_id,n.round FROM telegram_notices n "
                        "JOIN requests r ON r.id=n.task_id "
                        "WHERE n.message_id=? AND r.actor=? AND r.source='telegram'",
                        (reply_to.message_id, scope),
                    ).fetchone()
                    if notice:
                        current_round = db.execute(
                            "SELECT COUNT(*) FROM replies WHERE task_id=?", (notice["task_id"],)
                        ).fetchone()[0]
                        if notice["round"] != current_round:
                            return await self.record_response(
                                update,
                                "That question has already been answered. Reply to the latest one.",
                            )
                        selected = notice["task_id"]
                waiting = db.execute(
                    "SELECT id FROM requests WHERE actor=? AND source='telegram' "
                    "AND status='needs_clarification' ORDER BY created",
                    (scope,),
                ).fetchall()
                if selected:
                    task = self.application.request(selected)
                    if not task or task["actor"] != scope or task["source"] != "telegram":
                        return await self.record_response(
                            update, "Choose a waiting task from this Telegram conversation."
                        )
                    snapshot.update(task_id=selected, answer=snapshot.get("answer", text))
                elif not command and not snapshot["session"] and waiting:
                    if len(waiting) != 1:
                        return await self.record_response(
                            update,
                            "Choose a task: /answer <task-id> <answer>\n"
                            + "\n".join(row["id"] for row in waiting),
                        )
                    snapshot.update(task_id=waiting[0]["id"], answer=text)
                if command == "finishissue" or not snapshot["task_id"]:
                    snapshot["task_id"] = f"telegram:{update.update_id}"
                if not command and not snapshot["session"] and "answer" not in snapshot:
                    return None
                task = self.application.request(snapshot["task_id"])
                mode = (
                    task["mode"]
                    if task
                    else (snapshot["session"]["mode"] if snapshot["session"] else "Issue")
                )
                if not task and "/pull/" in text:
                    mode = "Pull Request"
                snapshot["mode"] = mode
                if "answer" in snapshot:
                    snapshot["answer"] = self.application.clean(snapshot["answer"])
                date = getattr(message, "date", None)
                created = date.timestamp() if date else time.time()
                # Telegram update IDs preserve order for same-second events.
                created += update.update_id / 1000000000000
                db.execute(
                    "INSERT INTO telegram_updates VALUES (?,?,?,?,?,?,?,NULL)",
                    (
                        update.update_id,
                        scope,
                        snapshot["task_id"],
                        mode,
                        json.dumps(snapshot),
                        self.application.clean(text),
                        created,
                    ),
                )
        # Redis must accept the user's message before any task is queued.
        await self.archive_update(update)
        response = snapshot.get("response")
        if response is None:
            response = await self.process(update, command, snapshot, text)
        await self.archive_update(update, response)
        with self.application.db() as db:
            db.execute(
                "UPDATE telegram_updates SET reply=? WHERE id=?", (response, update.update_id)
            )
        return response

    async def process(self, update, command, snapshot, text):
        scope = self.scope(update)
        tid = snapshot["task_id"]
        session = snapshot["session"]
        if "answer" in snapshot:
            try:
                task = await self.application.answer(
                    tid, f"telegram:{update.update_id}", scope, "telegram", snapshot["answer"]
                )
            except ConfigError as error:
                # Archive errors must retry this update, never acknowledge a lost answer.
                if "saved" in str(error) or "saving" in str(error):
                    raise
                return str(error)
            return f"Task {task['id']} continuing: {task['status']}."
        with self.application.db() as db:
            if command == "cancel":
                db.execute("DELETE FROM channel_sessions WHERE scope=?", (scope,))
                db.execute("DELETE FROM telegram_drafts WHERE scope=?", (scope,))
                return "Input cancelled. Accepted tasks continue in the background."
            if command == "finishissue":
                db.execute(
                    "INSERT OR REPLACE INTO channel_sessions VALUES (?,?,?,?)",
                    (scope, None, "Issue", time.time() + 900),
                )
                db.execute("INSERT OR REPLACE INTO telegram_drafts VALUES (?,?)", (scope, tid))
                return "Send a GitHub repository, issue or pull request link."
        if session["expires"] < time.time():
            return "Input expired. Send /FinishIssue again."
        if not text or text.startswith("/"):
            return "Send a link and task description, or /cancel."
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
        task = await self.application.submit(
            tid, scope, "telegram", mode, link, text, archive_user=False
        )
        with self.application.db() as db:
            db.execute("DELETE FROM channel_sessions WHERE scope=?", (scope,))
            db.execute("DELETE FROM telegram_drafts WHERE scope=?", (scope,))
        return f"Task {task['id']} accepted: {task['status']}."


async def send_results(application, bot, stop):
    import asyncio

    from telegram.error import BadRequest, Forbidden, TelegramError

    while not stop.is_set():
        with application.db() as db:
            rows = db.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM replies WHERE task_id=r.id) AS round "
                "FROM requests r WHERE r.source='telegram' AND r.status IN "
                "('completed','failed','interrupted','timed_out','delivered_unverified',"
                "'needs_clarification')"
            ).fetchall()
        for row in rows:
            key = (row["id"], row["round"], row["status"])
            with application.db() as db:
                notice = db.execute(
                    "SELECT * FROM telegram_notices WHERE task_id=? AND round=? AND status=?",
                    key,
                ).fetchone()
            if notice and (notice["delivered"] or notice["retry_at"] > time.time()):
                continue
            if row["status"] == "interrupted":
                try:
                    application.select_task_connections(row)
                    await application.save_result(row)
                except ConfigError:
                    continue  # Keep retrying archival before claiming a visible notification.
            result = row["result"] or ""
            try:
                parsed = json.loads(result)
                result = (
                    parsed.get("summary", "")
                    + "\n"
                    + ("PR: " + parsed["pr_url"] + "\n" if parsed.get("pr_url") else "")
                    + "Validation: "
                    + parsed.get("validation", "Not passed")
                )
            except (ValueError, AttributeError, TypeError):
                pass
            text = f"Task {row['id']}: {row['status'].replace('_', ' ')}\n{result}"
            if row["status"] == "needs_clarification":
                text += f"\nReply to this message or use /answer {row['id']} <answer>."
            chat, _, thread = row["actor"].split(":")
            with application.db() as db:
                db.execute(
                    "INSERT INTO telegram_notices(task_id,round,status,attempts,retry_at) "
                    "VALUES (?,?,?,1,?) ON CONFLICT(task_id,round,status) "
                    "DO UPDATE SET attempts=attempts+1,retry_at=excluded.retry_at",
                    (
                        *key,
                        time.time() + min(60, 3 * 2 ** min(notice["attempts"] if notice else 0, 5)),
                    ),
                )
            delivered, message_id = 0, None
            try:
                sent = await bot.send_message(
                    chat_id=int(chat),
                    message_thread_id=int(thread) or None,
                    text=application.clean(text)[:4000],
                )
                delivered, message_id = 1, sent.message_id
            except (BadRequest, Forbidden):
                delivered = -1  # Permanent rejection remains visible in the local journal.
            except TelegramError:
                pass  # Never acknowledge; retry with bounded backoff, including after restart.
            with application.db() as db:
                db.execute(
                    "UPDATE telegram_notices SET delivered=?,message_id=? "
                    "WHERE task_id=? AND round=? AND status=?",
                    (delivered, message_id, *key),
                )
        try:
            await asyncio.wait_for(stop.wait(), 3)
        except TimeoutError:
            pass
