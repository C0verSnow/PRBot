"""Shared desktop/Telegram submission, durable execution claims and archive retries.

SQLite is the local execution journal, not the source of remote history. The SDK
alone owns repository edits and delivery; no second controller commits its work.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from prbot.codex_adapter import CodexAdapter, parent_rules
from prbot.config import ConfigError, workspace_directory
from prbot.conversation_store import ConversationStore
from prbot.execution_setup import codex_login, github_token
from prbot.paths import data_dir
from prbot.secrets import require_secrets

RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {key: {"type": "string"} for key in ("summary", "pr_url", "validation")},
    "required": ["summary", "pr_url", "validation", "outcome"],
}

RESULT_SCHEMA["properties"]["outcome"] = {
    "type": "string",
    "enum": ["completed", "needs_clarification", "failed"],
}


def target_link(value, mode):
    if mode not in {"Issue", "Pull Request"} or not isinstance(value, str):
        raise ConfigError("Choose Issue or Pull Request and enter a GitHub link.")
    url = urlsplit(value.strip())
    match = re.fullmatch(r"/([\w-]+)/([\w.-]+)(?:/(issues|pull)/([1-9][0-9]*))?/?", url.path)
    if (
        url.scheme != "https"
        or url.netloc.lower() != "github.com"
        or not match
        or url.query
        or url.fragment
    ):
        raise ConfigError("Enter a valid https://github.com/owner/repo issue or PR link.")
    owner, repo, kind, number = match.groups()
    repo = repo.removesuffix(".git")
    if repo in {"", ".", ".."} or (mode == "Pull Request" and kind != "pull"):
        raise ConfigError("Pull Request requires a GitHub /pull/number link.")
    if mode == "Issue" and kind == "pull":
        raise ConfigError("Use the Pull Request tab for a PR link.")
    return f"{owner}/{repo}", kind or "repository", int(number) if number else None


def redact(text, secrets):
    for secret in sorted({s for s in secrets if isinstance(s, str)}, key=len, reverse=True):
        if isinstance(secret, str) and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"\b\d{5,20}:[A-Za-z0-9_-]{20,}", "[REDACTED]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)", "[REDACTED]", text)
    return text


class ConversationService:
    def __init__(self, archive=None, adapter=None, values=None):
        self.values = (
            values
            if values is not None
            else require_secrets("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")
        )
        self.archive = archive or ConversationStore(self.values)
        self.adapter = adapter
        self.runtime_secrets = []
        self.path = data_dir() / "workbench.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, actor TEXT, source TEXT, mode TEXT,
                    conversation TEXT, status TEXT, payload TEXT, result TEXT,
                    pid INTEGER, created REAL, changed REAL);
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    scope TEXT PRIMARY KEY, link TEXT, mode TEXT, expires REAL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def clean(self, text):
        secrets = list(self.values.values()) + self.runtime_secrets
        try:
            from prbot.secrets import bootstrap

            secrets.extend(bootstrap().values())
        except ConfigError:
            pass
        try:
            auth = json.loads(codex_login())
            secrets.extend(auth.get("tokens", {}).values())
        except ConfigError:
            pass
        return redact(text, secrets)

    @staticmethod
    def conversation(actor, source, mode):
        return hashlib.sha256(f"{source}:{actor}:{mode}".encode()).hexdigest()[:32]

    def request(self, request_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    async def submit(self, request_id, actor, source, mode, link, text):
        if (
            source not in {"desktop", "telegram"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{8,100}", request_id)
            or not isinstance(text, str)
            or not 8 <= len(text.strip()) <= 12000
        ):
            raise ConfigError("Describe the required change in 8 to 12000 characters.")
        repo, kind, number = target_link(link, mode)
        conversation = self.conversation(actor, source, mode)
        existing = self.request(request_id)
        if existing:
            if existing["actor"] != actor or existing["source"] != source:
                raise ConfigError("Request belongs to a different conversation.")
            return self.public(existing)
        payload = self.clean(
            json.dumps(
                {"repo": repo, "kind": kind, "number": number, "link": link, "text": text.strip()}
            )
        )
        now = time.time()
        message = {
            "conversation_id": conversation,
            "message_id": request_id + ":user",
            "source": source,
            "actor_id": actor,
            "role": "user",
            "content": json.loads(payload)["text"],
            "created_at": now,
            "delivery_status": "saved",
            "task_id": request_id,
            "sequence": now,
        }
        # Archive before accepting. A failed write leaves the same request safe to retry.
        await self.archive.save(message)
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    actor,
                    source,
                    mode,
                    conversation,
                    "queued",
                    payload,
                    None,
                    None,
                    now,
                    now,
                ),
            )
        return self.public(self.request(request_id))

    def public(self, row):
        return {
            "id": row["id"],
            "conversation_id": row["conversation"],
            "mode": row["mode"],
            "status": row["status"],
            "result": row["result"],
            "created_at": row["created"],
        }

    def tasks(self, actor, source="desktop"):
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM requests WHERE actor=? AND source=? ORDER BY created DESC LIMIT 100",
                (actor, source),
            ).fetchall()
            return [self.public(row) for row in rows]

    def change(self, task_id, status, result=None):
        with self.db() as db:
            db.execute(
                "UPDATE requests SET status=?,result=COALESCE(?,result),changed=? WHERE id=?",
                (status, result, time.time(), task_id),
            )

    async def save_result(self, row):
        message = {
            "conversation_id": row["conversation"],
            "message_id": row["id"] + ":assistant",
            "source": row["source"],
            "actor_id": row["actor"],
            "role": "assistant",
            "content": row["result"],
            "created_at": row["changed"],
            "delivery_status": "saved",
            "task_id": row["id"],
            "sequence": row["created"] + 0.001,
        }
        await self.archive.save(message)
        outcome = json.loads(row["result"]).get("outcome", "failed")
        self.change(row["id"], outcome if row["status"] == "save_pending" else row["status"])

    async def retry_save(self, task_id, actor):
        row = self.request(task_id)
        if not row or row["actor"] != actor or row["status"] != "save_pending":
            raise ConfigError("No pending result to save.")
        await self.save_result(row)
        return self.public(self.request(task_id))

    async def execute(self, row):
        tid = row["id"]
        try:
            root = workspace_directory()
            root.mkdir(parents=True, exist_ok=True)
            path = root / ("task-" + hashlib.sha256(tid.encode()).hexdigest()[:20])
            path.mkdir(exist_ok=True)
            payload = json.loads(row["payload"])
            history = []
            if not self.adapter:
                cursor = "0"
                while True:
                    page = await self.archive.history(row["conversation"], cursor)
                    history.extend(page["messages"])
                    cursor = page["cursor"]
                    if cursor == "0":
                        break
                history.sort(key=lambda message: message["sequence"])
                history = history[-20:]
            # The SDK owns GitHub auth via host gh/git credentials (or Infisical GITHUB_TOKEN).
            if self.adapter:
                adapter = self.adapter
            else:
                import base64

                token = github_token(self.values)
                self.runtime_secrets.append(token)
                basic = base64.b64encode(("x-access-token:" + token).encode()).decode()
                adapter = CodexAdapter(
                    codex_login(),
                    environment={
                        "GH_TOKEN": token,
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": "Authorization: Basic " + basic,
                    },
                )
            prompt = json.dumps(
                {
                    "instruction": "Complete this GitHub task using host tools and Full Access. "
                    "Read the GitHub issue/PR and comments first, including linked requirements. "
                    "Use host git authentication and the GH_TOKEN environment variable "
                    "for GitHub REST. "
                    "Do not assume gh is installed. Never print the token. Clone or "
                    "synchronize the repository into "
                    "this task directory. Work on a feature branch. For an existing PR, fetch "
                    "its head and push requested fixes to that PR only if authorized and writable. "
                    "Never merge or approve a PR. Never run local builds, compilation tests or "
                    "smoke scripts; run required tests in existing remote CI and wait for them. "
                    "Update README, manual and timestamped four-quadrant tasklist.md. "
                    "For Issue tasks create a PR; close a linked issue only after checks succeed. "
                    "Report actual validation evidence and PR URL. Ask for clarification if "
                    "requirements are unclear. Never expose credentials in output or files.",
                    "task": payload,
                    "conversation_history": history,
                    "parent_rules": parent_rules(path),
                    "current_unix_time": int(time.time()),
                },
                ensure_ascii=False,
            )
            result = await adapter.invoke(
                path, prompt, RESULT_SCHEMA, int(self.values.get("PRBOT_TASK_TIMEOUT", 3600))
            )
            if result.get("outcome") == "completed" and not self.adapter:
                await self.verify_delivery(payload, result, token)
            text = self.clean(json.dumps(result, ensure_ascii=False))
            self.change(tid, "save_pending", text)
            await self.save_result(self.request(tid))
        except asyncio.CancelledError:
            self.change(
                tid,
                "interrupted",
                "Interrupted. Check the repository and PR before "
                "starting another task. Work was not automatically repeated.",
            )
            raise
        except Exception as error:
            if self.request(tid)["status"] == "save_pending":
                return  # Retry archival only; SDK has already finished.
            message = (
                str(error)
                if isinstance(error, ConfigError)
                else (
                    "Task failed. Check Codex sign-in, GitHub access and network, then review "
                    "the repository before submitting again."
                )
            )
            result = self.clean(
                json.dumps(
                    {
                        "outcome": "failed",
                        "summary": message,
                        "pr_url": "",
                        "validation": "Not passed",
                    }
                )
            )
            self.change(tid, "save_pending", result)
            try:
                await self.save_result(self.request(tid))
            except ConfigError:
                pass

    async def verify_delivery(self, payload, result, token):
        from prbot.github import DeliveryError, GitHub

        repo, kind, number = target_link(result.get("pr_url"), "Pull Request")
        if repo.lower() != payload["repo"].lower():
            raise ConfigError("Delivery PR does not belong to the requested repository.")
        if payload["kind"] == "pull" and number != payload["number"]:
            raise ConfigError("Delivery must reference the requested pull request.")
        github = GitHub(token)
        try:
            pr = await github.request("GET", f"/repos/{repo}/pulls/{number}")
            if pr.get("merged") or pr.get("state") != "open":
                raise ConfigError("Delivery PR is not open for review.")
            required = tuple(
                v.strip()
                for v in str(self.values.get("PRBOT_REQUIRED_CHECKS", "test,dmg")).split(",")
                if v.strip()
            )
            if not required or not await github.checks(repo, pr["head"]["sha"], required):
                raise ConfigError("Remote checks have not passed. Review the PR Checks tab.")
            current = await github.request("GET", f"/repos/{repo}/pulls/{number}")
            if current["head"]["sha"] != pr["head"]["sha"]:
                raise ConfigError("PR changed during verification. Recheck the latest commit.")
        except DeliveryError:
            raise ConfigError(
                "GitHub delivery could not be verified. Review the PR Checks tab."
            ) from None
        finally:
            await github.close()

    async def run(self, stop):
        while not stop.is_set():
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                # Dead process claims become Interrupted, never silently re-executed.
                for row in db.execute("SELECT id,pid FROM requests WHERE status='running'"):
                    try:
                        os.kill(row["pid"], 0)
                    except (ProcessLookupError, TypeError):
                        db.execute(
                            "UPDATE requests SET status='interrupted',result=? WHERE id=?",
                            ("Interrupted. Review repository state before retrying.", row["id"]),
                        )
                row = db.execute(
                    "SELECT * FROM requests WHERE status='queued' "
                    "AND NOT EXISTS (SELECT 1 FROM requests WHERE status='running') "
                    "ORDER BY created LIMIT 1"
                ).fetchone()
                claimed = (
                    row
                    and db.execute(
                        "UPDATE requests SET status='running',pid=? WHERE id=? AND status='queued'",
                        (os.getpid(), row["id"]),
                    ).rowcount
                )
            if claimed:
                await self.execute(dict(row))
            else:
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
