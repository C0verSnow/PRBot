"""Durable conversations, generic task sources and an independent reply outbox."""

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

ACTIVE = ("queued", "running", "validating", "publishing")


def repository_url(value):
    try:
        url = urlsplit(value.strip())
        if (
            url.scheme != "https"
            or url.netloc.lower() != "github.com"
            or url.username
            or url.password
        ):
            raise ValueError
        match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)/?", url.path)
        if not match:
            raise ValueError
        owner, repo = match.groups()
        repo = repo.removesuffix(".git")
        if repo in {"", ".", ".."}:
            raise ValueError
        return f"{owner}/{repo}".lower()
    except ValueError:
        raise ValueError("请输入有效的 https://github.com/用户名/仓库名 链接。") from None


@dataclass(frozen=True)
class Source:
    kind: str = "repository"
    number: int | None = None


class TaskStore:
    def __init__(self, state):
        self.db = state.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                scope TEXT PRIMARY KEY, phase TEXT, repo TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS task_records (
                id TEXT PRIMARY KEY, update_id INTEGER UNIQUE, scope TEXT,
                dedup TEXT, status TEXT, payload TEXT, created REAL, changed REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_task_dedup ON task_records(dedup)
                WHERE status IN ('queued','running','validating','publishing');
            CREATE TABLE IF NOT EXISTS saved_replies (
                update_id INTEGER PRIMARY KEY, text TEXT);
            CREATE TABLE IF NOT EXISTS task_outbox (
                id TEXT PRIMARY KEY, chat_id INTEGER, thread_id INTEGER, text TEXT,
                status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0);
        """)

    def reply(self, uid):
        row = self.db.execute("SELECT text FROM saved_replies WHERE update_id=?", (uid,)).fetchone()
        return row[0] if row else None

    def save_reply(self, uid, reply):
        self.db.execute("INSERT OR IGNORE INTO saved_replies VALUES (?,?)", (uid, reply))

    def session(self, scope):
        return self.db.execute(
            "SELECT phase,repo,expires FROM conversations WHERE scope=?", (scope,)
        ).fetchone()

    def wait(self, scope, phase, repo, ttl):
        self.db.execute(
            "INSERT OR REPLACE INTO conversations VALUES (?,?,?,?)",
            (scope, phase, repo, time.time() + ttl),
        )

    def cancel(self, scope):
        self.db.execute("DELETE FROM conversations WHERE scope=?", (scope,))

    def get(self, task_id):
        row = self.db.execute(
            "SELECT status,payload FROM task_records WHERE id=?", (task_id,)
        ).fetchone()
        return dict(json.loads(row[1]), id=task_id, status=row[0]) if row else None

    def submit(self, uid, scope, repo, requirement, message, source=None, context=None):
        source = source or Source()
        # Called inside the same SQLite transaction as conversation/reply updates.
        existing = self.db.execute(
            "SELECT id FROM task_records WHERE update_id=?", (uid,)
        ).fetchone()
        digest = hashlib.sha256(
            json.dumps([scope, repo, source.__dict__, requirement], ensure_ascii=False).encode()
        ).hexdigest()
        if not existing:
            existing = self.db.execute(
                "SELECT id FROM task_records WHERE dedup=? AND status IN (?,?,?,?)",
                (digest, *ACTIVE),
            ).fetchone()
        if existing:
            return self.get(existing[0])
        task_id = uuid.uuid4().hex[:16]
        payload = dict(
            repo=repo,
            requirement=requirement,
            source=source.__dict__,
            context=context,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            thread_id=message.message_thread_id,
            snapshot_at=time.time(),
            branch=f"feature/prbot-{task_id}",
            base_sha=None,
            pr_url=None,
            issue_result=(
                f"https://github.com/{repo}/issues/{source.number}：尚未关闭"
                if source.kind == "issue"
                else "无关联 Issue"
            ),
            validation=None,
        )
        now = time.time()
        self.db.execute(
            "INSERT INTO task_records VALUES (?,?,?,?,?,?,?,?)",
            (task_id, uid, scope, digest, "queued", json.dumps(payload), now, now),
        )
        return self.get(task_id)

    def change(self, task_id, status, **changes):
        task = self.get(task_id)
        task.update(changes)
        task.pop("id")
        task.pop("status")
        with self.db:
            self.db.execute(
                "UPDATE task_records SET status=?,payload=?,changed=? WHERE id=?",
                (status, json.dumps(task), time.time(), task_id),
            )

    def list_status(self, *statuses):
        rows = self.db.execute(
            "SELECT id FROM task_records WHERE status IN (" + ",".join("?" for _ in statuses) + ")",
            statuses,
        ).fetchall()
        return [self.get(row[0]) for row in rows]

    def notify(self, task):
        text = (
            f"任务 {task['id']}：{task['status']}\n"
            f"仓库：https://github.com/{task['repo']}\n"
            f"PR：{task.get('pr_url') or '尚未创建'}\n"
            f"变更：{task.get('summary') or '尚未交付'}\n"
            f"验证：{task.get('validation') or '未通过'}\n"
            f"Issue：{task.get('issue_result')}\n合并：未执行\n"
            f"{task.get('error') or ''}"
        )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO task_outbox(id,chat_id,thread_id,text) VALUES (?,?,?,?)",
                (
                    task["id"] + ":" + task["status"],
                    task["chat_id"],
                    task["thread_id"],
                    text[:4000],
                ),
            )


class Conversations:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.github = None
        self.contexts = {}

    async def handle_async(self, update, command=None):
        message = update.message
        if (
            not self.github
            or not message.from_user
            or message.from_user.id not in self.settings.allowed_users
            or self.store.reply(update.update_id) is not None
            or command
        ):
            return self.handle(update, command)
        scope = f"{message.chat.id}:{message.from_user.id}:{message.message_thread_id or 0}"
        session = self.store.session(scope)
        if not session or session[2] < time.time():
            return self.handle(update, command)
        phase, repo, _ = session
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return None
        if phase == "repository":
            parts = text.split(maxsplit=1)
            try:
                repo = repository_url(parts[0])
            except ValueError:
                return self.handle(update, command)
            if len(parts) < 2:
                return self.handle(update, command)
            text = parts[1]
        if repo not in self.settings.allowed_repos or len(text) < 8:
            return self.handle(update, command)
        from prbot.github import DeliveryError

        try:
            repository = await self.github.repository(repo)
            source = Source()
            issue_context = None
            issue = re.search(r"https://github\.com/([^/\s]+/[^/\s]+)/issues/([0-9]+)", text)
            if issue:
                if issue[1].lower() != repo:
                    raise DeliveryError("关联 Issue 必须属于同一个允许的目标仓库。")
                source = Source("issue", int(issue[2]))
                issue_context = await self.github.request(
                    "GET", f"/repos/{repo}/issues/{source.number}"
                )
                if "pull_request" in issue_context:
                    raise DeliveryError("该链接是 PR；当前入口仅接收仓库需求和关联 Issue。")
            self.contexts[update.update_id] = (repository, source, issue_context)
        except DeliveryError as error:
            with self.store.db:
                reply = str(error)
                self.store.save_reply(update.update_id, reply)
            return reply
        try:
            return self.handle(update, command)
        finally:
            self.contexts.pop(update.update_id, None)

    def handle(self, update, command=None):
        message = update.message
        if not message.from_user:
            return "无法识别发起用户。"
        scope = f"{message.chat.id}:{message.from_user.id}:{message.message_thread_id or 0}"
        if message.from_user.id not in self.settings.allowed_users:
            return "当前用户未被允许执行仓库任务。" if command else None
        saved = self.store.reply(update.update_id)
        if saved is not None:
            return saved
        # One transaction: accepting an update cannot leave the session advanced but reply lost.
        with self.store.db:
            reply = self._handle(update, scope, command)
            if reply is not None:
                self.store.save_reply(update.update_id, reply)
            return reply

    def _handle(self, update, scope, command):
        if command == "finishissue":
            self.store.wait(scope, "repository", None, self.settings.session_ttl)
            return (
                "请输入 GitHub 仓库链接，例如：https://github.com/C0verSnow/PRBot；"
                "发送 /cancel 可取消。"
            )
        if command == "cancel":
            if self.store.session(scope):
                self.store.cancel(scope)
                return "已取消等待输入。"
            active = [
                t
                for t in self.store.list_status(*ACTIVE)
                if f"{t['chat_id']}:{t['user_id']}:{t['thread_id'] or 0}" == scope
            ]
            if active:
                return (
                    f"任务 {active[0]['id']}：{active[0]['status']}，已进入后台，/cancel 不适用。"
                )
            return "没有等待输入的会话。"
        session = self.store.session(scope)
        if not session:
            return None
        phase, repo, expires = session
        if time.time() > expires:
            self.store.cancel(scope)
            return "等待已过期，请重新发送 /FinishIssue。"
        text = (update.message.text or "").strip()
        if not text or text.startswith("/"):
            return None
        if phase == "repository":
            parts = text.split(maxsplit=1)
            try:
                repo = repository_url(parts[0])
            except ValueError as error:
                return str(error)
            if repo not in self.settings.allowed_repos:
                return "此仓库不在允许操作的范围，请联系管理员配置。"
            if len(parts) == 1:
                self.store.wait(scope, "requirement", repo, self.settings.session_ttl)
                return "请提供具体需求，包括要改什么、预期结果和如何验收。"
            text = parts[1]
        if len(text) < 8:
            return "需求还不够具体，请说明要修改的文件或行为，以及预期结果。"
        context_data = self.contexts.get(update.update_id)
        repository, source, issue_context = context_data or (None, Source(), None)
        task = self.store.submit(
            update.update_id, scope, repo, text, update.message, source, issue_context
        )
        context = repository
        if context:
            payload = dict(task, repository=context)
            payload.pop("id")
            payload.pop("status")
            self.store.db.execute(
                "UPDATE task_records SET payload=? WHERE id=?", (json.dumps(payload), task["id"])
            )
        self.store.cancel(scope)
        return f"任务 {task['id']}：已受理（{task['status']}），正在后台校验仓库和需求。"
