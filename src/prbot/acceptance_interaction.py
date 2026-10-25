"""Real SDK question/answer and real Redis transport recovery in remote CI."""

import json
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from prbot.acceptance_network import GatedArchive, NetworkGate
from prbot.codex_adapter import CodexAdapter
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService
from prbot.execution_setup import codex_login


def require(condition, message):
    if not condition:
        raise ConfigError(message)


async def check_interaction(values, fields, evidence):
    rid = "acceptance-dialogue-" + uuid.uuid4().hex
    reply = "acceptance-answer-" + uuid.uuid4().hex
    marker = uuid.uuid4().hex
    gate = NetworkGate(values["UPSTASH_REDIS_REST_URL"])
    async with gate.serve():
        with tempfile_context() as outside:
            archive = GatedArchive(values, gate)
            native = CodexAdapter(codex_login())
            application = None

            class DialogueAdapter:
                calls = 0

                async def invoke(self, path, prompt, schema, timeout):
                    self.calls += 1
                    context = json.loads(prompt)["continuation"]
                    instruction = (
                        "This is a verification-only provider acceptance, not a GitHub task. "
                        "Do not edit repositories, publish anything, run tests/builds "
                        "or read secrets. "
                        "Return the supplied JSON schema with an empty pr_url. "
                    )
                    if not context["answers"]:
                        instruction += (
                            "Before doing any work, ask which marker to write. Return outcome "
                            "needs_clarification, summary containing this question, and validation "
                            "stating you are waiting for the user's answer. "
                            "Do not create files yet."
                        )
                    else:
                        instruction += (
                            "Continue the original task using the saved question and user answer "
                            "below. Write exactly the marker from the user's answer into "
                            f"{outside / 'answer.txt'} using a host tool, then read it back. "
                            "Return outcome completed only after confirming its content. "
                            "Saved context: " + json.dumps(context)
                        )
                    result = await native.invoke(path, instruction, schema, min(timeout, 240))
                    if context["answers"]:
                        # Drop the real HTTPS route after SDK completion, before archiving.
                        gate.disconnect()
                    return result

            adapter = DialogueAdapter()
            application = ConversationService(archive=archive, adapter=adapter, values=values)

            async def on_event(event):
                application.event(rid, event["type"], event.get("text", ""))
                evidence["sdk_incremental_events"] = True

            native.on_event = on_event
            evidence["sdk_incremental_events"] = False
            # New desktop tasks use the request ID as their conversation ID.
            conversation = rid
            fields.extend(
                conversation + ":" + message
                for message in (
                    rid + ":user",
                    rid + ":assistant",
                    rid + ":assistant:1",
                    reply + ":user",
                )
            )
            try:
                await application.submit(
                    rid,
                    rid,
                    "desktop",
                    "Issue",
                    "https://github.com/C0verSnow/PRBot",
                    "Ask which marker to write, then use my answer in the same task.",
                )
                await application.execute(application.request(rid))
                require(
                    application.request(rid)["status"] == "needs_clarification",
                    "Real SDK did not enter the waiting-for-answer state.",
                )
                require(
                    not (outside / "answer.txt").exists(), "SDK acted before receiving an answer."
                )
                application = ConversationService(archive=archive, adapter=adapter, values=values)
                require(
                    application.tasks(rid)[0]["status"] == "needs_clarification",
                    "Waiting task did not survive service reopening.",
                )
                evidence["real_sdk_question_restored"] = True
                accepted = await application.answer(rid, reply, rid, "desktop", marker)
                require(accepted["id"] == rid, "Answer was associated with another task.")
                await application.execute(application.request(rid))
                require(
                    application.request(rid)["status"] == "save_pending",
                    "Disconnected Redis did not preserve the pending SDK result.",
                )
                evidence["redis_network_loss_preserves_result"] = True
                gate.connected = True
                await application.retry_save(rid, rid)
                require(
                    application.request(rid)["status"] == "completed", "Result recovery failed."
                )
                require(
                    (outside / "answer.txt").read_text().strip() == marker, "Answer was not used."
                )
                require(adapter.calls == 2, "Archival recovery reran the SDK.")
                tasks = application.tasks(rid)
                require(
                    len(tasks) == 1 and tasks[0]["id"] == rid, "Continuation created another task."
                )
                evidence["real_sdk_same_task_answer"] = True
                evidence["redis_network_recovery_without_sdk_replay"] = True
                messages, cursor = [], "0"
                while True:
                    page = await archive.history(conversation, cursor)
                    messages.extend(page["messages"])
                    cursor = page["cursor"]
                    if cursor == "0":
                        break
                require(
                    len({m["message_id"] for m in messages}) == 4, "Dialogue archive incomplete."
                )
                require(marker in {m["content"] for m in messages}, "Answer absent from history.")
                evidence["real_dialogue_history_restored"] = True
            finally:
                gate.connected = True
                with application.db() as db:
                    for table, column in (
                        ("requests", "id"),
                        ("task_events", "task_id"),
                        ("replies", "task_id"),
                    ):
                        db.execute(f"DELETE FROM {table} WHERE {column}=?", (rid,))


@contextmanager
def tempfile_context():
    with tempfile.TemporaryDirectory(prefix="prbot-dialogue-evidence-") as directory:
        yield Path(directory)
