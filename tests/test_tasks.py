import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.config import ConfigError
from prbot.conversation_service import ConversationService, redact, target_link
from prbot.conversation_store import ConversationStore
from prbot.telegram_conversations import TelegramConversations


@pytest.fixture
def app():
    archive = Mock(save=AsyncMock(), history=AsyncMock())
    adapter = Mock(
        invoke=AsyncMock(
            return_value={
                "summary": "Implemented",
                "pr_url": "https://github.com/o/r/pull/1",
                "validation": "Remote checks passed",
                "outcome": "completed",
            }
        )
    )
    return ConversationService(archive=archive, adapter=adapter, values={})


@pytest.mark.parametrize(
    "mode,link,expected",
    [
        ("Issue", "https://github.com/o/r", ("o/r", "repository", None)),
        ("Issue", "https://github.com/o/r/issues/9", ("o/r", "issues", 9)),
        ("Pull Request", "https://github.com/o/r/pull/7", ("o/r", "pull", 7)),
    ],
)
def test_target_modes(mode, link, expected):
    assert target_link(link, mode) == expected


@pytest.mark.parametrize(
    "mode,link",
    [
        ("Issue", "https://github.com.evil/o/r"),
        ("Issue", "http://github.com/o/r"),
        ("Issue", "https://user@github.com/o/r"),
        ("Issue", "https://github.com/o/r/pull/1"),
        ("Pull Request", "https://github.com/o/r"),
        ("Pull Request", "https://github.com/o/r/issues/1"),
        ("Issue", "https://github.com/o/r?x=1"),
        ("Issue", "https://github.com/o/.."),
    ],
)
def test_invalid_links(mode, link):
    with pytest.raises(ConfigError):
        target_link(link, mode)


async def submit(app, rid="request-123"):
    return await app.submit(
        rid,
        "local:me",
        "desktop",
        "Issue",
        "https://github.com/o/r/issues/9",
        "Implement the requested change",
    )


async def test_retry_does_not_repeat_acceptance_or_sdk(app):
    first = await submit(app)
    assert await submit(app) == first
    assert first["status"] == "queued"
    app.archive.save.assert_awaited_once()
    app.adapter.invoke.assert_not_awaited()
    await app.execute(app.request(first["id"]))
    assert app.request(first["id"])["status"] == "completed"
    assert (await submit(app))["status"] == "completed"
    app.adapter.invoke.assert_awaited_once()


async def test_archive_failure_prevents_execution_and_preserves_retry_id(app):
    app.archive.save.side_effect = ConfigError("Message not saved. Retry.")
    with pytest.raises(ConfigError, match="Message not saved"):
        await submit(app)
    assert app.request("request-123") is None
    app.adapter.invoke.assert_not_awaited()
    app.archive.save.side_effect = None
    assert (await submit(app))["id"] == "request-123"


async def test_result_archive_retry_never_runs_sdk_twice(app):
    await submit(app)
    app.archive.save.side_effect = ConfigError("Message not saved. Retry.")
    await app.execute(app.request("request-123"))
    assert app.request("request-123")["status"] == "save_pending"
    result = app.request("request-123")["result"]
    app.archive.save.side_effect = None
    await app.retry_save("request-123", "local:me")
    assert app.request("request-123")["status"] == "completed"
    assert app.request("request-123")["result"] == result
    app.adapter.invoke.assert_awaited_once()


async def test_restart_retains_pending_result_and_restores_tasks(app):
    await submit(app)
    app.change("request-123", "save_pending", json.dumps({"outcome": "completed"}))
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values={})
    assert reopened.tasks("local:me")[0]["status"] == "save_pending"
    await reopened.retry_save("request-123", "local:me")
    app.adapter.invoke.assert_not_awaited()


async def test_other_actor_cannot_reuse_request_or_save_result(app):
    await submit(app)
    with pytest.raises(ConfigError):
        await app.submit(
            "request-123",
            "other",
            "desktop",
            "Issue",
            "https://github.com/o/r",
            "Implement required change",
        )
    app.change("request-123", "save_pending", "{}")
    with pytest.raises(ConfigError):
        await app.retry_save("request-123", "other")
    assert not app.tasks("other")


async def test_cancellation_marks_interrupted_and_never_requeues(app):
    await submit(app)
    app.adapter.invoke.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await app.execute(app.request("request-123"))
    assert app.request("request-123")["status"] == "interrupted"
    assert (await submit(app))["status"] == "interrupted"


async def test_failed_sdk_diagnostics_do_not_echo_credentials(app):
    await submit(app)
    app.adapter.invoke.side_effect = RuntimeError("Bearer private-token")
    await app.execute(app.request("request-123"))
    assert app.request("request-123")["status"] == "failed"
    assert "private-token" not in app.request("request-123")["result"]


async def test_clarification_is_not_completed_delivery(app):
    await submit(app)
    app.adapter.invoke.return_value = {
        "outcome": "needs_clarification",
        "summary": "Which file?",
        "pr_url": "",
        "validation": "",
    }
    await app.execute(app.request("request-123"))
    assert app.request("request-123")["status"] == "needs_clarification"


async def test_worker_owns_claim_and_stops_active_sdk(app):
    await submit(app)
    entered = asyncio.Event()

    async def block(*args):
        entered.set()
        await asyncio.Event().wait()

    app.adapter.invoke.side_effect = block
    runner = asyncio.create_task(app.run(asyncio.Event()))
    await asyncio.wait_for(entered.wait(), 2)
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    assert app.request("request-123")["status"] == "interrupted"


async def test_bound_telegram_and_desktop_share_service_without_identity_bypass(app, update):
    flow = TelegramConversations(app)
    assert "not bound" in await flow.handle_async(update(), "finishissue")
    app.values["PRBOT_TELEGRAM_ACTOR_ID"] = "123"
    assert "Send a GitHub" in await flow.handle_async(update(), "finishissue")
    assert "Describe" in await flow.handle_async(update("https://github.com/o/r/pull/7", uid=11))
    reply = await flow.handle_async(update("Review and fix this pull request", uid=12))
    assert "accepted" in reply
    row = app.request("telegram:12")
    assert row["mode"] == "Pull Request"
    assert row["source"] == "telegram"
    assert row["conversation"] != app.conversation("local:me", "desktop", "Pull Request")


def test_redaction_including_unicode_and_known_secret():
    assert redact("私有内容 secret-long-token", ["secret-long-token"]) == "私有内容 [REDACTED]"
    assert "ghp_" not in redact("ghp_abcdefghijklmnopqrstuvwxyz", [])
    assert "123456:" not in redact("123456:" + "a" * 35, [])


async def test_redis_capacity_and_transport_errors_are_actionable():
    store = ConversationStore(
        {
            "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
            "UPSTASH_REDIS_REST_TOKEN": "private",
        }
    )
    store.command = AsyncMock(return_value=-1)
    with pytest.raises(ConfigError, match="10 MB"):
        await store.save({"conversation_id": "c", "message_id": "m", "content": "你好"})
    assert store.command.call_args.args[-1] == 10_000_000


async def test_redis_pagination_orders_messages_and_exposes_cursor():
    store = ConversationStore(
        {
            "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
            "UPSTASH_REDIS_REST_TOKEN": "private",
        }
    )
    store.command = AsyncMock(
        return_value=[
            "11",
            [
                "b",
                json.dumps({"sequence": 2, "message_id": "b"}),
                "a",
                json.dumps({"sequence": 1, "message_id": "a"}),
            ],
        ]
    )
    page = await store.history("conversation", "4")
    assert page["cursor"] == "11"
    assert [m["message_id"] for m in page["messages"]] == ["a", "b"]


async def test_two_runners_do_not_execute_the_same_task(app):
    await submit(app)
    entered, release = asyncio.Event(), asyncio.Event()

    async def run_once(*args):
        entered.set()
        await release.wait()
        return {"summary": "done", "outcome": "completed", "pr_url": "", "validation": "passed"}

    app.adapter.invoke.side_effect = run_once
    stop = asyncio.Event()
    other = ConversationService(archive=app.archive, adapter=app.adapter, values={})
    runners = [asyncio.create_task(service.run(stop)) for service in (app, other)]
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.sleep(0.6)
    assert app.adapter.invoke.await_count == 1
    release.set()
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.gather(*runners)
    assert app.request("request-123")["status"] == "completed"


async def test_orphaned_claim_is_interrupted_even_when_pid_has_been_reused(app):
    import os

    await submit(app)
    with app.db() as db:
        db.execute("UPDATE requests SET status='running',pid=?", (os.getpid(),))
    stop = asyncio.Event()
    runner = asyncio.create_task(app.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await runner
    assert app.request("request-123")["status"] == "interrupted"
    app.adapter.invoke.assert_not_awaited()


async def test_accepted_task_keeps_selected_workspace_when_preference_changes(app, tmp_path):
    from prbot.task_config import select_work_root

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    select_work_root(str(first))
    await submit(app)
    select_work_root(str(second))
    await app.execute(app.request("request-123"))
    path = app.adapter.invoke.call_args.args[0]
    assert path == first
    assert not (second / "PRBot-workspaces").exists()
    assert not list(first.glob("task-*"))


async def test_telegram_runner_cannot_claim_desktop_id_token_override(app):
    await submit(app)
    stop = asyncio.Event()
    runner = asyncio.create_task(app.run(stop, source="telegram"))
    await asyncio.sleep(0.1)
    stop.set()
    await runner
    assert app.request("request-123")["status"] == "queued"
    app.adapter.invoke.assert_not_awaited()


async def test_archive_retry_selects_the_accepted_configuration(app, tmp_path, monkeypatch):
    from prbot.task_config import select_work_root

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    select_work_root(str(first))
    await submit(app)
    select_work_root(str(second))
    selected = Mock(return_value={})
    archive = Mock(save=AsyncMock())
    monkeypatch.setattr("prbot.conversation_service.require_secrets", selected)
    monkeypatch.setattr("prbot.conversation_service.ConversationStore", lambda values: archive)
    app.adapter = None
    app.change("request-123", "save_pending", json.dumps({"outcome": "completed"}))
    await app.retry_save("request-123", "local:me")
    assert selected.call_args.kwargs["configuration"] == str(first / ".env")
    archive.save.assert_awaited_once()


async def test_missing_old_configuration_never_archives_to_the_new_project(app, monkeypatch):
    await submit(app)
    app.adapter = None

    def unavailable(row):
        raise ConfigError("Original Infisical configuration unavailable.")

    monkeypatch.setattr(app, "select_task_connections", unavailable)
    await app.execute(app.request("request-123"))
    assert app.request("request-123")["status"] == "save_pending"
    assert "Original Infisical" in app.request("request-123")["result"]
    assert app.archive.save.await_count == 1


@pytest.mark.parametrize("text", ["做", "fix", "完成issue即可", "1234567"])
async def test_short_nonempty_descriptions_are_accepted(app, text):
    task = await app.submit(
        "short-request", "local:me", "desktop", "Issue", "https://github.com/o/r", text
    )
    assert task["status"] == "queued"
    assert json.loads(app.request(task["id"])["payload"])["text"] == text


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 12001])
async def test_empty_or_oversized_descriptions_are_rejected(app, text):
    with pytest.raises(ConfigError):
        await app.submit(
            "empty-request", "local:me", "desktop", "Issue", "https://github.com/o/r", text
        )
    app.archive.save.assert_not_awaited()


@pytest.mark.parametrize(
    "checks,expected",
    [(False, "pending"), (True, "passed"), ("failure", "failed"), ("network", "unknown")],
)
async def test_delivery_preserves_pr_and_closed_issue_when_checks_not_passed(
    app, monkeypatch, checks, expected
):
    from prbot.github import DeliveryError

    github = Mock(
        request=AsyncMock(
            side_effect=[
                {"state": "open", "head": {"sha": "head"}},
                {"state": "closed"},
                {"state": "open", "head": {"sha": "head"}},
            ]
        ),
        checks=AsyncMock(return_value=checks),
        close=AsyncMock(),
    )
    if checks == "failure":
        github.checks.side_effect = DeliveryError(
            "Required remote checks failed. Open the PR Checks tab."
        )
    if checks == "network":
        github.checks.side_effect = DeliveryError("GitHub request unconfirmed.")
    monkeypatch.setattr("prbot.github.GitHub", lambda token: github)
    result = {
        "summary": "Implemented",
        "outcome": "completed",
        "pr_url": "https://github.com/o/r/pull/1",
        "validation": "SDK reports success",
    }
    await app.verify_delivery({"repo": "o/r", "kind": "issues", "number": 9}, result, "token")
    assert result["delivery"]["pr"] == "open"
    assert result["delivery"]["issue"] == "closed"
    assert result["delivery"]["ci"] == expected
    assert result["outcome"] == ("completed" if checks is True else "delivered_unverified")
    assert result["pr_url"] == "https://github.com/o/r/pull/1"
    assert result["delivery"]["code"] == "completed_by_codex"
    github.close.assert_awaited_once()
