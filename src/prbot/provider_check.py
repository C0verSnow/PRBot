"""Opt-in remote acceptance: real Infisical, Redis and Full Access SDK turn.

No credentials or provider responses are printed. Run only in a dedicated CI
job with temporary encrypted inputs; the caller removes those inputs afterward.
"""

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from prbot.codex_adapter import CodexAdapter
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService
from prbot.conversation_store import MAX_BYTES, SAVE, ConversationStore
from prbot.execution_setup import codex_login
from prbot.github import GitHub
from prbot.secrets import require_secrets


async def check():
    if os.environ.get("CI") != "true":
        raise ConfigError("Provider acceptance is only allowed in remote CI.")
    bootstrap = json.loads(os.environ.pop("PRBOT_ACCEPTANCE_BOOTSTRAP"))
    auth = os.environ.pop("PRBOT_ACCEPTANCE_CODEX_AUTH")
    os.environ.update({k: v for k, v in bootstrap.items() if k.startswith("INFISICAL_")})
    auth_path = Path.home() / ".codex/auth.json"
    if auth_path.exists():
        raise ConfigError("Acceptance requires a clean runner without an existing Codex login.")
    auth_path.parent.mkdir(mode=0o700, exist_ok=True)
    auth_path.write_text(auth)
    auth_path.chmod(0o600)
    evidence = {"started_at": int(time.time())}
    archive = None
    fields = []
    scratch_keys = []
    try:
        values = require_secrets("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")
        archive = ConversationStore(values)
        evidence["infisical"] = True
        evidence["redis_ping"] = await archive.command("PING") == "PONG"
        with tempfile.TemporaryDirectory(prefix="prbot-full-access-evidence-") as temporary:
            outside = Path(temporary)
            native_adapter = CodexAdapter(codex_login())

            class EvidenceAdapter:
                calls = 0

                async def invoke(self, path, prompt, schema, timeout):
                    self.calls += 1
                    instruction = (
                        "This is a real provider acceptance task. Do not modify any repository, "
                        "open a PR, send messages or run tests/builds. Ignore the general delivery "
                        "instruction for this verification-only request. Use a file editing tool "
                        f"to write exactly write-ok to {outside / 'write.txt'}. "
                        "Then use a host shell command to write exactly command-ok "
                        f"to {outside / 'command.txt'}. Read both files and confirm their content. "
                        "Both paths are outside the working directory; Full Access is required. "
                        "Return the requested JSON schema: outcome completed, summary describing "
                        "the observed file and command results, empty pr_url, "
                        "validation describing "
                        "what actually ran. Never disclose login or environment secrets."
                    )
                    return await native_adapter.invoke(path, instruction, schema, timeout)

            adapter = EvidenceAdapter()
            application = ConversationService(archive=archive, adapter=adapter, values=values)
            rid = "acceptance-" + uuid.uuid4().hex
            actor = rid
            requirement = "Verify Full Access host file writes and shell commands in remote CI."
            task = await application.submit(
                rid, actor, "desktop", "Issue", "https://github.com/C0verSnow/PRBot", requirement
            )
            fields = [
                task["conversation_id"] + ":" + rid + suffix for suffix in (":user", ":assistant")
            ]
            await application.execute(application.request(rid))
            row = application.request(rid)
            if row["status"] != "completed":
                evidence["sdk_status"] = row["status"]
                diagnostic = json.loads(row["result"] or "{}")
                evidence["sdk_error_code"] = diagnostic.get("error_code", "unknown")
                evidence["sdk_stage"] = diagnostic.get("stage", "unknown")
                raise ConfigError("Real SDK acceptance failed. Inspect the safe task category.")
            evidence["host_file_write"] = (outside / "write.txt").read_text().strip() == "write-ok"
            evidence["host_shell_command"] = (
                outside / "command.txt"
            ).read_text().strip() == "command-ok"
            replay = await application.submit(
                rid, actor, "desktop", "Issue", "https://github.com/C0verSnow/PRBot", requirement
            )
            evidence["idempotent_sdk_execution"] = adapter.calls == 1 and replay["id"] == rid
            reopened = ConversationService(
                archive=ConversationStore(values), adapter=adapter, values=values
            )
            cursor, messages = "0", []
            while True:
                page = await reopened.archive.history(task["conversation_id"], cursor)
                messages.extend(page["messages"])
                cursor = page["cursor"]
                if cursor == "0":
                    break
            evidence["redis_history_restored"] = {m["role"] for m in messages} == {
                "user",
                "assistant",
            }
            encoded = json.dumps(messages)
            evidence["no_credentials_in_history"] = all(
                secret not in encoded for secret in values.values() if len(secret) > 8
            )
            evidence["complete_local_login"] = bool(
                json.loads(codex_login())["tokens"]["access_token"]
            )
            with application.db() as db:
                db.execute("DELETE FROM requests WHERE id=?", (rid,))

        prefix = "prbot:acceptance:" + uuid.uuid4().hex
        scratch_keys = [prefix + ":messages", prefix + ":bytes"]
        small = await archive.command("EVAL", SAVE, 2, *scratch_keys, "one", "hello", 10)
        duplicate = await archive.command("EVAL", SAVE, 2, *scratch_keys, "one", "hello", 10)
        over = await archive.command("EVAL", SAVE, 2, *scratch_keys, "two", "hello", 10)
        evidence["atomic_budget_and_dedup"] = (small, duplicate, over) == (1, 0, -1)
        evidence["configured_capacity_bytes"] = MAX_BYTES
        github = GitHub(os.environ["GH_TOKEN"])
        try:
            repository = await github.request("GET", "/repos/C0verSnow/PRBot")
            evidence["github_authenticated_read"] = repository["full_name"] == "C0verSnow/PRBot"
        finally:
            await github.close()
        if not all(value for value in evidence.values() if isinstance(value, bool)):
            raise ConfigError("Provider acceptance assertions failed.")
        evidence["ok"] = True
    finally:
        auth_path.unlink(missing_ok=True)
        if archive:
            if fields:
                # Remove only this run's fixtures and decrement the exact payload counter.
                cleanup = """
                local removed = 0
                for i, field in ipairs(ARGV) do
                  local value = redis.call('HGET', KEYS[1], field)
                  if value then
                    removed = removed + string.len(field) + string.len(value)
                    redis.call('HDEL', KEYS[1], field)
                  end
                end
                if removed > 0 then redis.call('DECRBY', KEYS[2], removed) end
                return removed
                """
                await archive.command(
                    "EVAL", cleanup, 2, "prbot:history:v1", "prbot:history:bytes:v1", *fields
                )
            if scratch_keys:
                await archive.command("DEL", *scratch_keys)
        evidence["finished_at"] = int(time.time())
        output = Path(os.environ["PRBOT_ACCEPTANCE_OUTPUT"])
        output.write_text(json.dumps(evidence, indent=2))


def run():
    try:
        asyncio.run(check())
    except Exception as error:
        # Safe metadata only. Never print raw provider exception bodies or SDK task output.
        print("Provider acceptance failed: " + type(error).__name__)
        raise SystemExit(2) from None
    print("Provider acceptance passed; sanitized evidence saved.")
