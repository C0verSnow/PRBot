"""Remote regression checks for Issue #11 task ownership and streaming boundaries."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.codex_adapter import CodexAdapter
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService


@pytest.fixture
def app():
    return ConversationService(
        archive=Mock(save=AsyncMock(), history=AsyncMock()),
        adapter=Mock(
            invoke=AsyncMock(
                return_value={
                    "summary": "Which branch should I use?",
                    "outcome": "needs_clarification",
                    "pr_url": "",
                    "validation": "Waiting for user",
                }
            )
        ),
        values={},
    )


async def waiting(app):
    await app.submit(
        "request-one",
        "local:me",
        "desktop",
        "Issue",
        "https://github.com/o/r/issues/11",
        "Update the client",
    )
    await app.execute(app.request("request-one"))


async def test_infisical_root_path_does_not_corrupt_task_target_or_workspace(
    app, monkeypatch, tmp_path
):
    monkeypatch.setenv("PRBOT_WORK_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "prbot.secrets.bootstrap",
        lambda: {"INFISICAL_SECRET_PATH": "/", "INFISICAL_CLIENT_SECRET": "tiny"},
    )
    await app.submit(
        "path-regression",
        "local:me",
        "desktop",
        "Issue",
        "https://github.com/o/r/issues/11",
        "Fix a/b without exposing tiny",
    )
    payload = json.loads(app.request("path-regression")["payload"])
    assert payload["workspace_root"] == str(tmp_path)
    assert payload["repo"] == "o/r"
    assert payload["link"] == "https://github.com/o/r/issues/11"
    assert payload["text"] == "Fix a/b without exposing [REDACTED]"
    await app.execute(app.request("path-regression"))
    assert app.adapter.invoke.call_args.args[0] == tmp_path


def test_long_infisical_secret_path_is_configuration_not_credential(app, monkeypatch):
    monkeypatch.setattr(
        "prbot.secrets.bootstrap",
        lambda: {"INFISICAL_SECRET_PATH": "/staging/project", "INFISICAL_CLIENT_SECRET": "tiny"},
    )
    assert app.clean("Path /staging/project; secret tiny") == (
        "Path /staging/project; secret [REDACTED]"
    )


async def test_answer_resumes_original_task_with_question_and_answer_after_restart(app):
    await waiting(app)
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values={})
    task = reopened.tasks("local:me")[0]
    assert task["status"] == "needs_clarification"
    assert task["events"][-1]["kind"] == "interaction"
    accepted = await reopened.answer(
        "request-one", "reply-one", "local:me", "desktop", "feature/ui"
    )
    assert accepted["id"] == "request-one"
    assert accepted["status"] == "queued"
    await reopened.execute(reopened.request("request-one"))
    prompt = json.loads(app.adapter.invoke.call_args.args[1])
    assert prompt["task"]["text"] == "Update the client"
    assert prompt["continuation"]["questions"][0]["text"] == "Which branch should I use?"
    assert prompt["continuation"]["answers"][0]["text"] == "feature/ui"
    assert len(reopened.tasks("local:me")) == 1
    assistant_ids = [
        call.args[0]["message_id"]
        for call in app.archive.save.await_args_list
        if call.args[0]["role"] == "assistant"
    ]
    assert len(set(assistant_ids)) == 2


async def test_answer_retry_is_idempotent_and_rejects_other_actor_or_channel(app):
    await waiting(app)
    for actor, source in [("other", "desktop"), ("local:me", "telegram")]:
        with pytest.raises(ConfigError):
            await app.answer("request-one", "reply-one", actor, source, "answer")
    await app.answer("request-one", "reply-one", "local:me", "desktop", "answer")
    await app.answer("request-one", "reply-one", "local:me", "desktop", "answer")
    with pytest.raises(ConfigError):
        await app.answer("request-one", "reply-two", "local:me", "desktop", "other answer")
    assert len(app.continuation(app.request("request-one"))["answers"]) == 1


async def test_unsaved_answer_does_not_resume_or_lose_question(app):
    await waiting(app)
    app.archive.save.side_effect = ConfigError("Message not saved")
    with pytest.raises(ConfigError):
        await app.answer("request-one", "reply-one", "local:me", "desktop", "answer")
    assert app.request("request-one")["status"] == "needs_clarification"
    assert not app.continuation(app.request("request-one"))["answers"]


async def test_chat_has_no_github_delivery_and_separate_history_scope(app):
    app.verify_delivery = AsyncMock()
    await app.submit("private-chat", "local:me", "desktop", "Chat History", "", "Explain this idea")
    await app.execute(app.request("private-chat"))
    prompt = json.loads(app.adapter.invoke.call_args.args[1])
    assert prompt["task"]["kind"] == "chat"
    assert "Do not execute GitHub tasks" in prompt["instruction"]
    assert "create a PR" not in prompt["instruction"]
    app.verify_delivery.assert_not_awaited()
    assert app.conversation("local:me", "desktop", "Chat History") != app.conversation(
        "local:me", "desktop", "Issue"
    )
    with pytest.raises(ConfigError):
        await app.submit("telegram-chat", "local:me", "telegram", "Chat History", "", "question")


async def test_timeout_retains_distinct_public_state(app):
    await app.submit("request-one", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix")
    app.adapter.invoke.side_effect = TimeoutError
    await app.execute(app.request("request-one"))
    assert app.tasks("local:me")[0]["status"] == "timed_out"
    assert json.loads(app.request("request-one")["result"])["error_code"] == "timeout"


def test_events_redact_credentials_and_bound_output(app):
    app.values["SECRET"] = "a-private-environment-secret"
    app.event("task", "text", "a-private-environment-secret ghp_abc123 Bearer abcdefghi")
    with app.db() as db:
        text = db.execute("SELECT text FROM task_events").fetchone()[0]
    assert "a-private" not in text and "ghp_" not in text and "abcdefghi" not in text
    app.event("task", "command", "untrusted command")
    for index in range(250):
        app.event("task", "progress", str(index))
    with app.db() as db:
        assert db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 200


async def test_adapter_delivers_event_before_worker_finishes(tmp_path, monkeypatch):
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    stdin = Mock(write=Mock(), drain=AsyncMock(), close=Mock())
    process = Mock(
        pid=123456, returncode=0, wait=AsyncMock(), stdin=stdin, stdout=stdout, stderr=stderr
    )
    monkeypatch.setattr(
        "prbot.codex_adapter.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr("prbot.codex_adapter.os.killpg", Mock())
    observed = asyncio.Event()

    async def progress(event):
        assert event == {"type": "progress", "text": "Working"}
        observed.set()

    task = asyncio.create_task(
        CodexAdapter("{}", on_event=progress).invoke(tmp_path, "task", {}, 5)
    )
    stdout.feed_data(b'{"type":"progress","text":"Working"}\n')
    await asyncio.wait_for(observed.wait(), 1)
    assert not task.done(), "Progress must arrive before the terminal result"
    stdout.feed_data(b'{"type":"result","result":{"summary":"Done"},"auth":"{}"}\n')
    stdout.feed_eof()
    stderr.feed_eof()
    assert await task == {"summary": "Done"}


def test_worker_commentary_filter_includes_short_sensitive_values(monkeypatch):
    from prbot.sdk_worker import safe_text

    monkeypatch.setenv("EXAMPLE_PASSWORD", "tiny")
    assert "tiny" not in safe_text("value=tiny Bearer abcdefgh", {"auth": "{}"})
    assert "abcdefgh" not in safe_text("value=tiny Bearer abcdefgh", {"auth": "{}"})


@pytest.mark.parametrize("native_questions", [False, True])
def test_worker_streams_safe_commentary_and_omits_tool_output(
    tmp_path, monkeypatch, capsys, native_questions
):
    import io
    import sys
    from types import SimpleNamespace as NS

    from prbot import sdk_worker

    runtime = tmp_path / "codex"
    runtime.touch(mode=0o700)
    response = {"summary": "Done", "outcome": "completed", "validation": "", "pr_url": ""}
    events = [
        NS(method="item/started", payload=NS()),
        NS(
            method="item/completed",
            payload=NS(item=NS(type="commandExecution", aggregated_output="RAW-TOOL-SECRET")),
        ),
        NS(
            method="item/completed",
            payload=NS(
                item=NS(
                    type="agentMessage",
                    phase="commentary",
                    text="Reviewing Bearer top-secret-token",
                )
            ),
        ),
        NS(
            method="item/completed",
            payload=NS(
                item=NS(
                    type="agentMessage",
                    phase="final_answer",
                    text=json.dumps(response),
                    questions=[NS(title="Which branch?", options=["feature/ui", "feature/api"])]
                    if native_questions
                    else None,
                )
            ),
        ),
        NS(method="turn/completed", payload=NS(turn=NS(status=NS(value="completed")))),
    ]
    client = Mock()
    client.account.return_value = NS(account=object())
    client.thread_start.return_value.turn.return_value.stream.return_value = iter(events)
    sdk = Mock()
    sdk.Codex.return_value.__enter__ = Mock(return_value=client)
    sdk.Codex.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    monkeypatch.setitem(sys.modules, "codex_cli_bin", Mock(bundled_codex_path=lambda: runtime))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "path": str(tmp_path),
                    "auth": "{}",
                    "prompt": '{"task":{"kind":"chat"}}',
                    "schema": {},
                }
            )
            + "\n"
        ),
    )
    sdk_worker.run()
    output = capsys.readouterr().out
    assert "RAW-TOOL-SECRET" not in output and "top-secret-token" not in output
    envelopes = [json.loads(line) for line in output.splitlines()]
    assert [event["type"] for event in envelopes] == (
        ["progress", "text", "interaction", "result"]
        if native_questions
        else ["progress", "text", "result"]
    )
    assert all(event["v"] == 1 for event in envelopes)
    if native_questions:
        assert envelopes[-1]["result"]["outcome"] == "needs_clarification"
        assert "Which branch?" in envelopes[-1]["result"]["summary"]
        assert "feature/ui" in envelopes[-1]["result"]["summary"]
    else:
        assert envelopes[-1]["result"] == response
    assert client.thread_start.call_args.kwargs["sandbox"] == sdk.Sandbox.read_only


def test_short_token_values_are_redacted_before_journaling(app):
    app.values["SERVICE_TOKEN"] = "tiny"
    app.event("task", "text", "private value: tiny")
    with app.db() as db:
        assert "tiny" not in db.execute("SELECT text FROM task_events").fetchone()[0]


def test_json_escaped_credentials_are_redacted_before_archive(app):
    secret = 'a-private-"secret\nvalue'
    app.values["SERVICE_TOKEN"] = secret
    encoded = app.clean(json.dumps({"summary": "value: " + secret}))
    assert json.loads(encoded)["summary"] == "value: [REDACTED]"


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
async def test_invalid_task_timeout_names_configuration_key(app, value):
    app.values["PRBOT_TASK_TIMEOUT"] = value
    await app.submit(
        "timeout-test", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix"
    )
    await app.execute(app.request("timeout-test"))
    row = app.request("timeout-test")
    assert row["status"] == "failed"
    assert "PRBOT_TASK_TIMEOUT" in json.loads(row["result"])["summary"]
    app.adapter.invoke.assert_not_awaited()
