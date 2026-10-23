"""Shared desktop/Telegram submission, durable execution claims and archive retries.

SQLite is the local execution journal, not the source of remote history. The SDK
alone owns repository edits and delivery; no second controller commits its work.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from prbot.codex_adapter import CodexAdapter, SDKFailure, parent_rules
from prbot.config import ConfigError, env_path, workspace_directory
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
    text = re.sub(r"(?i)\bBearer\s+[^\s\"<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
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
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
                    kind TEXT, text TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS replies (
                    id TEXT PRIMARY KEY, task_id TEXT, text TEXT, created REAL);
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
        values = dict(self.values)
        secrets = list(values.values()) + self.runtime_secrets
        sensitive = list(self.runtime_secrets)
        try:
            from prbot.secrets import bootstrap

            values.update(bootstrap())
            secrets.extend(values.values())
        except ConfigError:
            pass
        sensitive.extend(
            value
            for key, value in values.items()
            if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD"))
        )
        try:
            auth = json.loads(codex_login())
            sensitive.extend(auth.get("tokens", {}).values())
        except ConfigError:
            pass
        variants = {
            variant
            for value in sensitive
            if isinstance(value, str) and value
            for variant in (
                value,
                json.dumps(value, ensure_ascii=False)[1:-1],
                json.dumps(value, ensure_ascii=True)[1:-1],
            )
        }
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
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
            or not 1 <= len(text.strip()) <= 12000
        ):
            raise ConfigError("Enter a non-empty description of up to 12000 characters.")
        if mode == "Chat History" and source == "desktop":
            repo, kind, number = "", "chat", None
        else:
            repo, kind, number = target_link(link, mode)
        conversation = self.conversation(actor, source, mode)
        existing = self.request(request_id)
        if existing:
            if existing["actor"] != actor or existing["source"] != source:
                raise ConfigError("Request belongs to a different conversation.")
            return self.public(existing)
        payload = self.clean(
            json.dumps(
                {
                    "repo": repo,
                    "kind": kind,
                    "number": number,
                    "link": link,
                    "text": text.strip(),
                    "workspace_root": str(workspace_directory()),
                    "configuration_file": str(env_path()),
                }
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
        with self.db() as db:
            events = [
                dict(event)
                for event in db.execute(
                    "SELECT sequence,kind,text,created FROM task_events WHERE task_id=? "
                    "ORDER BY sequence DESC LIMIT 100",
                    (row["id"],),
                )
            ][::-1]
        return {
            "id": row["id"],
            "conversation_id": row["conversation"],
            "mode": row["mode"],
            "status": row["status"],
            "result": row["result"],
            "created_at": row["created"],
            "events": events,
        }

    def tasks(self, actor, source="desktop"):
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM requests WHERE actor=? AND source=? ORDER BY created DESC LIMIT 100",
                (actor, source),
            ).fetchall()
            return [self.public(row) for row in rows]

    def event(self, task_id, kind, text):
        if kind not in {"progress", "text", "interaction", "status"}:
            return
        text = self.clean(str(text))[:8000]
        with self.db() as db:
            previous = db.execute(
                "SELECT text FROM task_events WHERE task_id=? ORDER BY sequence DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if not previous or previous["text"] != text:
                db.execute(
                    "INSERT INTO task_events(task_id,kind,text,created) VALUES (?,?,?,?)",
                    (task_id, kind, text, time.time()),
                )
                db.execute(
                    "DELETE FROM task_events WHERE task_id=? AND kind!='interaction' "
                    "AND sequence NOT IN (SELECT sequence FROM task_events WHERE task_id=? "
                    "ORDER BY sequence DESC LIMIT 200)",
                    (task_id, task_id),
                )

    async def answer(self, task_id, reply_id, actor, source, text):
        row = self.request(task_id)
        if (
            not row
            or row["actor"] != actor
            or row["source"] != source
            or not isinstance(reply_id, str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{8,100}", reply_id)
            or not isinstance(text, str)
            or not 1 <= len(text.strip()) <= 12000
        ):
            raise ConfigError("Choose your waiting task and enter an answer.")
        with self.db() as db:
            previous = db.execute("SELECT task_id FROM replies WHERE id=?", (reply_id,)).fetchone()
        if previous:
            if previous["task_id"] != task_id:
                raise ConfigError("Answer belongs to another task.")
            return self.public(row)
        if row["status"] != "needs_clarification":
            raise ConfigError("This task is no longer waiting for an answer. Refresh tasks.")
        cleaned = self.clean(text.strip())
        now = time.time()
        await self.archive.save(
            {
                "conversation_id": row["conversation"],
                "message_id": reply_id + ":user",
                "source": source,
                "actor_id": actor,
                "role": "user",
                "content": cleaned,
                "created_at": now,
                "delivery_status": "saved",
                "task_id": task_id,
                "sequence": now,
            }
        )
        with self.db() as db:
            # A double click or concurrent reply cannot queue the same turn twice.
            changed = db.execute(
                "UPDATE requests SET status='queued',changed=? WHERE id=? "
                "AND status='needs_clarification'",
                (now, task_id),
            ).rowcount
            if not changed:
                raise ConfigError("Another answer was accepted. Refresh this task.")
            db.execute("INSERT INTO replies VALUES (?,?,?,?)", (reply_id, task_id, cleaned, now))
        self.event(task_id, "status", "Answer accepted. Continuing this task with saved context.")
        return self.public(self.request(task_id))

    def change(self, task_id, status, result=None):
        with self.db() as db:
            db.execute(
                "UPDATE requests SET status=?,result=COALESCE(?,result),changed=? WHERE id=?",
                (status, result, time.time(), task_id),
            )

    def select_task_connections(self, row):
        if not self.adapter:
            configuration = json.loads(row["payload"]).get("configuration_file")
            self.values = require_secrets(
                "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", configuration=configuration
            )
            self.archive = ConversationStore(self.values)

    async def save_result(self, row):
        with self.db() as db:
            round_number = db.execute(
                "SELECT COUNT(*) FROM replies WHERE task_id=?", (row["id"],)
            ).fetchone()[0]
        suffix = ":" + str(round_number) if round_number else ""
        message = {
            "conversation_id": row["conversation"],
            "message_id": row["id"] + ":assistant" + suffix,
            "source": row["source"],
            "actor_id": row["actor"],
            "role": "assistant",
            "content": row["result"],
            "created_at": row["changed"],
            "delivery_status": "saved",
            "task_id": row["id"],
            "sequence": row["changed"],
        }
        await self.archive.save(message)
        outcome = json.loads(row["result"]).get("outcome", "failed")
        if outcome == "needs_clarification":
            self.event(row["id"], "interaction", json.loads(row["result"]).get("summary", ""))
        self.change(
            row["id"],
            ("timed_out" if json.loads(row["result"]).get("error_code") == "timeout" else outcome)
            if row["status"] == "save_pending"
            else row["status"],
        )

    async def retry_save(self, task_id, actor):
        row = self.request(task_id)
        if not row or row["actor"] != actor or row["status"] != "save_pending":
            raise ConfigError("No pending result to save.")
        self.select_task_connections(row)
        await self.save_result(row)
        return self.public(self.request(task_id))

    async def execute(self, row):
        tid = row["id"]
        archive_ready = bool(self.adapter)
        try:
            self.select_task_connections(row)
            archive_ready = True
            payload = json.loads(row["payload"])
            root = Path(payload["workspace_root"])
            if root.is_symlink():
                raise ConfigError("Working directory cannot be a symlink.")
            root.mkdir(parents=True, exist_ok=True)
            path = root
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

                token = github_token(self.values) if payload["kind"] != "chat" else ""
                self.runtime_secrets.append(token)
                basic = base64.b64encode(("x-access-token:" + token).encode()).decode()
                self.runtime_secrets.append(basic)
                adapter = CodexAdapter(
                    codex_login(),
                    environment={
                        "GH_TOKEN": token,
                        "GITHUB_TOKEN": token,
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": "Authorization: Basic " + basic,
                    },
                )
            prompt = json.dumps(
                {
                    "instruction": (
                        "Answer this private conversation. Do not publish it to GitHub. "
                        if payload["kind"] == "chat"
                        else "Complete this GitHub task using host tools and Full Access. "
                    )
                    + "Read the GitHub issue/PR and comments first, including linked requirements. "
                    "Use host git authentication and the GH_TOKEN environment variable "
                    "for GitHub REST. "
                    "Do not assume gh is installed. Never print the token. Clone or "
                    "synchronize the repository into a repository-named folder under "
                    "the selected working directory (owner/repository when needed). Reuse an "
                    "existing matching checkout only when safe; preserve user changes. Never "
                    "name folders after task IDs or create a PRBot-specific workspace root. "
                    "Work on a feature branch. For an existing PR, fetch "
                    "its head and push requested fixes to that PR only if authorized and writable. "
                    "Never merge or approve a PR. Never run local builds, compilation tests or "
                    "smoke scripts; run required tests in existing remote CI and wait for them. "
                    "Update README, manual and timestamped four-quadrant tasklist.md. "
                    "For Issue tasks create a PR; close a linked issue only after checks succeed. "
                    "Report actual validation evidence and PR URL. Ask for clarification if "
                    "requirements are unclear. Never expose credentials in output or files.",
                    "task": payload,
                    "conversation_history": history,
                    "continuation": self.continuation(row),
                    "privacy": (
                        "Private chat: answer the user only. Do not create, publish or "
                        "update GitHub issues, PRs or comments. Do not run GitHub tasks."
                        if payload["kind"] == "chat"
                        else "GitHub workspace task."
                    ),
                    "parent_rules": parent_rules(path),
                    "current_unix_time": int(time.time()),
                },
                ensure_ascii=False,
            )
            if payload["kind"] == "chat":
                private_prompt = json.loads(prompt)
                private_prompt["instruction"] = (
                    "Answer this private conversation using the saved history. "
                    "Do not execute GitHub tasks, edit repositories, create or publish issues, "
                    "PRs or comments. Return summary and outcome using the supplied schema. "
                    "If you need information, return needs_clarification and ask in summary."
                )
                prompt = json.dumps(private_prompt, ensure_ascii=False)
            if not self.adapter:

                async def on_event(event):
                    self.event(tid, event["type"], event.get("text", ""))

                adapter.on_event = on_event
            self.event(tid, "status", "Codex started.")
            result = await adapter.invoke(
                path, prompt, RESULT_SCHEMA, int(self.values.get("PRBOT_TASK_TIMEOUT", 3600))
            )
            if (
                not self.adapter
                and payload["kind"] != "chat"
                and (
                    result.get("outcome") == "completed"
                    or (result.get("pr_url") and result.get("outcome") != "needs_clarification")
                )
            ):
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
            if isinstance(error, TimeoutError):
                error = SDKFailure("timeout", "turn", "TimeoutError")
            if self.request(tid)["status"] == "save_pending":
                return  # Retry archival only; SDK has already finished.
            message = (
                str(error)
                if isinstance(error, (ConfigError, SDKFailure))
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
            if isinstance(error, SDKFailure):
                diagnostic = json.loads(result)
                diagnostic.update(
                    error_code=error.code, stage=error.stage, exception_type=error.exception_type
                )
                result = json.dumps(diagnostic)
            self.change(tid, "save_pending", result)
            if archive_ready:
                try:
                    await self.save_result(self.request(tid))
                except ConfigError:
                    pass

    def continuation(self, row):
        with self.db() as db:
            answers = [
                dict(answer)
                for answer in db.execute(
                    "SELECT text,created FROM replies WHERE task_id=? ORDER BY created,id",
                    (row["id"],),
                )
            ]
            questions = [
                dict(question)
                for question in db.execute(
                    "SELECT text,created FROM task_events WHERE task_id=? AND kind='interaction' "
                    "ORDER BY sequence",
                    (row["id"],),
                )
            ]
        return {
            "task_id": row["id"],
            "questions": questions,
            "answers": answers,
            "instruction": "Continue this original task using these answers. Inspect existing "
            "repository work before editing; do not repeat completed delivery operations.",
        }

    async def verify_delivery(self, payload, result, token):
        from prbot.github import CheckFailure, DeliveryError, GitHub

        code_completed = result.get("outcome") == "completed"
        delivery = {
            "code": "completed_by_codex" if code_completed else "not_confirmed",
            "pr": "unknown",
            "issue": "not_applicable",
            "ci": "unknown",
            "detail": "",
        }
        result["delivery"] = delivery
        result["outcome"] = "delivered_unverified"

        def note(message):
            delivery["detail"] = message
            result["validation"] = message

        try:
            repo, kind, number = target_link(result.get("pr_url"), "Pull Request")
            if repo.lower() != payload["repo"].lower():
                raise ConfigError("Delivery PR does not belong to the requested repository.")
            if payload["kind"] == "pull" and number != payload["number"]:
                raise ConfigError("Delivery must reference the requested pull request.")
        except ConfigError as error:
            # Preserve the SDK report, but never present an unrelated PR as verified delivery.
            delivery["pr"] = "invalid"
            result["outcome"] = "failed"
            note(str(error))
            return
        github = GitHub(token)
        if payload["kind"] == "issues":
            delivery["issue"] = "unknown"
        try:
            pr = await github.request("GET", f"/repos/{repo}/pulls/{number}")
            delivery["pr"] = "merged" if pr.get("merged") else pr.get("state", "unknown")
            delivery["head_sha"] = pr["head"]["sha"]
            if payload["kind"] == "issues":
                try:
                    issue = await github.request("GET", f"/repos/{repo}/issues/{payload['number']}")
                    delivery["issue"] = issue.get("state", "unknown")
                except DeliveryError:
                    pass  # Still inspect CI; a missing issue read must not hide PR facts.
            required = tuple(
                v.strip()
                for v in str(self.values.get("PRBOT_REQUIRED_CHECKS", "test,dmg")).split(",")
                if v.strip()
            )
            try:
                passed = bool(required) and await github.checks(repo, pr["head"]["sha"], required)
                delivery["ci"] = "passed" if passed else "pending"
                note(
                    "Required remote checks passed."
                    if passed
                    else "Required remote checks are pending or missing. Review the PR Checks tab."
                )
            except DeliveryError as error:
                # Terminal failures and unavailable API responses are different facts.
                delivery["ci"] = "failed" if isinstance(error, CheckFailure) else "unknown"
                note(
                    "Required remote checks failed. Review the PR Checks tab."
                    if delivery["ci"] == "failed"
                    else "Remote checks could not be read. Review the PR Checks tab."
                )
            current = await github.request("GET", f"/repos/{repo}/pulls/{number}")
            delivery["pr"] = "merged" if current.get("merged") else current.get("state", "unknown")
            if current["head"]["sha"] != pr["head"]["sha"]:
                delivery["ci"] = "pending"
                note("PR changed during verification. Recheck the latest commit.")
            if delivery["pr"] != "open":
                note("Delivery PR is not open for review. " + delivery["detail"])
            elif payload["kind"] == "issues" and delivery["issue"] != "closed":
                note("Target issue is " + delivery["issue"] + ". " + delivery["detail"])
            elif delivery["ci"] == "passed" and code_completed:
                result["outcome"] = "completed"
        except (DeliveryError, KeyError, TypeError):
            note("GitHub delivery could not be fully verified. Review the PR and Checks tab.")
        finally:
            await github.close()

    async def run(self, stop, source=None):
        # A kernel lock is released on process exit; unlike PID-only ownership it
        # cannot mistake a reused PID after reboot for a still-running SDK task.
        with open(data_dir() / "workbench-execution.lock", "a") as lock:
            while not stop.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    try:
                        await asyncio.wait_for(stop.wait(), 0.5)
                    except TimeoutError:
                        pass
                    continue
                try:
                    with self.db() as db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE requests SET status='interrupted',result=? "
                            "WHERE status='running'",
                            ("Interrupted. Review repository state before retrying.",),
                        )
                        row = db.execute(
                            "SELECT * FROM requests WHERE status='queued' "
                            "AND (? IS NULL OR source=?) ORDER BY created LIMIT 1",
                            (source, source),
                        ).fetchone()
                        if row:
                            db.execute(
                                "UPDATE requests SET status='running',pid=? WHERE id=?",
                                (os.getpid(), row["id"]),
                            )
                    if row:
                        await self.execute(dict(row))
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                if not row:
                    try:
                        await asyncio.wait_for(stop.wait(), 0.5)
                    except TimeoutError:
                        pass
