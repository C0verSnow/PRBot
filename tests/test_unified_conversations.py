# coding: utf-8
"""Issue #13 regressions, executed by remote CI (no local compilation)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.command_router import CommandRouter
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService
from prbot.conversation_store import ConversationStore
from prbot.telegram_auth import TelegramAuthorization, actor_ids
from prbot.telegram_conversations import TelegramConversations, send_results


@pytest.fixture
def app():
    archive = Mock(save=AsyncMock(), messages=AsyncMock(return_value=[]))
    adapter = Mock(
        invoke=AsyncMock(
            return_value={
                "summary": "Which PR?",
                "outcome": "needs_clarification",
                "validation": "Waiting",
                "pr_url": "",
            }
        )
    )
    return ConversationService(
        archive=archive, adapter=adapter, values={"PRBOT_TELEGRAM_ACTOR_ID": "123"}
    )


async def create(flow, update, uid=10):
    await flow.handle_async(update("/FinishIssue", uid=uid), "finishissue")
    await flow.handle_async(update("https://github.com/o/r/issues/13", uid=uid + 1))
    return await flow.handle_async(update("Finish this issue", uid=uid + 2))


async def test_creation_archive_and_telegram_answer_reach_original_sdk_after_restart(app, update):
    flow = TelegramConversations(app)
    assert "telegram:10" in await create(flow, update)
    await app.execute(app.request("telegram:10"))
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values=app.values)
    reply = await TelegramConversations(reopened).handle_async(update("新建PR5", uid=13))
    assert "telegram:10 continuing" in reply
    assert reopened.request("telegram:13") is None
    await reopened.execute(reopened.request("telegram:10"))
    prompt = json.loads(app.adapter.invoke.call_args.args[1])
    assert prompt["continuation"]["task_id"] == "telegram:10"
    assert prompt["continuation"]["answers"][0]["text"] == "新建PR5"
    messages = [call.args[0] for call in app.archive.save.await_args_list]
    assert {m["task_id"] for m in messages} == {"telegram:10"}
    assert any(m["content"] == "/FinishIssue" for m in messages)
    assert any(m["role"] == "assistant" and "Describe" in m["content"] for m in messages)
    assert any("Which PR?" in m["content"] for m in messages)


async def test_duplicate_update_after_restart_never_queues_second_answer(app, update):
    flow = TelegramConversations(app)
    await create(flow, update)
    await app.execute(app.request("telegram:10"))
    first = await flow.handle_async(update("Create PR5", uid=13))
    again = TelegramConversations(
        ConversationService(archive=app.archive, adapter=app.adapter, values=app.values)
    )
    assert await again.handle_async(update("Create PR5", uid=13)) == first
    assert len(app.continuation(app.request("telegram:10"))["answers"]) == 1
    assert app.request("telegram:10")["status"] == "queued"


async def test_multiple_waiting_tasks_require_selection_and_reject_foreign_task(app, update):
    flow = TelegramConversations(app)
    await create(flow, update)
    await app.execute(app.request("telegram:10"))
    await create(flow, update, uid=20)
    await app.execute(app.request("telegram:20"))
    assert "Choose a task" in await flow.handle_async(update("Create PR5", uid=23))
    router = CommandRouter("testbot", flow)
    reply = await router.dispatch_async(update("/answer telegram:20 Create PR5", uid=24))
    assert "telegram:20 continuing" in reply
    assert app.request("telegram:10")["status"] == "needs_clarification"
    assert app.request("telegram:20")["status"] == "queued"
    await app.submit(
        "foreign-task",
        "456:123:0",
        "telegram",
        "Issue",
        "https://github.com/o/r",
        "Other conversation",
    )
    assert "Choose a waiting task" in await flow.handle_async(
        update("/answer foreign-task secret", uid=25), "answer"
    )


async def test_reply_to_notice_beats_ambiguous_waiting_tasks(app, update):
    flow = TelegramConversations(app)
    for uid in (10, 20):
        await create(flow, update, uid)
        await app.execute(app.request(f"telegram:{uid}"))
    with app.db() as db:
        db.execute(
            "INSERT INTO telegram_notices(task_id,round,status,message_id,delivered) "
            "VALUES ('telegram:10',0,'needs_clarification',99,1)"
        )
    reply_to = {
        "message_id": 99,
        "date": 1700000000,
        "chat": {"id": 123, "type": "private"},
        "text": "Which PR?",
    }
    assert "telegram:10 continuing" in await flow.handle_async(
        update("PR5", uid=30, reply_to_message=reply_to)
    )
    assert app.request("telegram:20")["status"] == "needs_clarification"


async def test_archive_failure_does_not_accept_task_and_same_update_is_retryable(app, update):
    flow = TelegramConversations(app)
    await flow.handle_async(update("/FinishIssue"), "finishissue")
    app.archive.save.side_effect = ConfigError("Message not saved. Retry.")
    with pytest.raises(ConfigError):
        await flow.handle_async(update("https://github.com/o/r Fix", uid=11))
    assert app.request("telegram:10") is None
    app.archive.save.side_effect = None
    assert "accepted" in await flow.handle_async(update("https://github.com/o/r Fix", uid=11))


async def test_failed_answer_reservation_survives_restart_and_blocks_competing_answer(app):
    await app.submit(
        "waiting-task", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix"
    )
    await app.execute(app.request("waiting-task"))
    app.archive.save.side_effect = ConfigError("Message not saved. Retry.")
    with pytest.raises(ConfigError):
        await app.answer("waiting-task", "reply-original", "local:me", "desktop", "PR5")
    app.archive.save.side_effect = None
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values=app.values)
    count = app.archive.save.await_count
    with pytest.raises(ConfigError, match="being saved"):
        await reopened.answer("waiting-task", "reply-other", "local:me", "desktop", "PR6")
    assert app.archive.save.await_count == count
    await reopened.answer("waiting-task", "reply-original", "local:me", "desktop", "PR5")
    assert reopened.continuation(reopened.request("waiting-task"))["answers"][0]["text"] == "PR5"


async def test_concurrent_answers_only_archive_one_accepted_decision(app):
    await app.submit(
        "waiting-task", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix"
    )
    await app.execute(app.request("waiting-task"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_save(message):
        entered.set()
        await release.wait()

    app.archive.save.side_effect = slow_save
    first = asyncio.create_task(
        app.answer("waiting-task", "reply-one", "local:me", "desktop", "PR5")
    )
    await entered.wait()
    with pytest.raises(ConfigError):
        await app.answer("waiting-task", "reply-two", "local:me", "desktop", "PR6")
    release.set()
    await first
    assert len(app.continuation(app.request("waiting-task"))["answers"]) == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        ("123", {"123"}),
        ("123, 456", {"123", "456"}),
        ("123\n456", {"123", "456"}),
        ("", set()),
        ("123,invalid", set()),
        ("123,", set()),
    ],
)
def test_authorization_format(value, expected):
    assert actor_ids(value) == expected


async def test_runtime_new_grant_and_expired_revocation_and_failure_deny(app, monkeypatch):
    loader = Mock(return_value={"PRBOT_TELEGRAM_ACTOR_ID": "123,456"})
    monkeypatch.setattr("prbot.telegram_auth.load_secrets", loader)
    auth = TelegramAuthorization(app)
    assert await auth.allows("123")
    loader.assert_not_called()
    assert await auth.allows("456")
    assert loader.call_args.kwargs == {"refresh": True, "timeout": 5}
    auth.expires = 0
    auth.last_refresh = float("-inf")
    loader.return_value = {"PRBOT_TELEGRAM_ACTOR_ID": "456"}
    assert not await auth.allows("123")
    assert await auth.allows("456")
    auth.expires = 0
    auth.last_refresh = float("-inf")
    loader.side_effect = RuntimeError("credential-that-must-not-leak")
    assert not await auth.allows("456")
    assert app.values["PRBOT_TELEGRAM_ACTOR_ID"] == ""


async def test_unknown_authorization_flood_coalesces_and_invalid_config_denies(app, monkeypatch):
    loader = Mock(return_value={"PRBOT_TELEGRAM_ACTOR_ID": "invalid"})
    monkeypatch.setattr("prbot.telegram_auth.load_secrets", loader)
    auth = TelegramAuthorization(app)
    assert not any(await asyncio.gather(*(auth.allows(str(i)) for i in range(30))))
    assert loader.call_count == 1


async def test_notifications_include_each_round_and_reply_routing(app):
    await app.submit(
        "telegram:10", "123:123:0", "telegram", "Issue", "https://github.com/o/r", "Fix"
    )
    stop = asyncio.Event()
    sent = []

    async def send(**kwargs):
        sent.append(kwargs["text"])
        stop.set()
        return SimpleNamespace(message_id=len(sent))

    bot = Mock(send_message=AsyncMock(side_effect=send))
    for round_number in range(2):
        await app.execute(app.request("telegram:10"))
        stop.clear()
        await send_results(app, bot, stop)
        assert "Which PR?" in sent[-1]
        assert "/answer telegram:10" in sent[-1]
        await app.answer("telegram:10", f"reply-{round_number:08}", "123:123:0", "telegram", "PR5")
    assert len(sent) == 2
    with app.db() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM telegram_notices WHERE delivered=1").fetchone()[0] == 2
        )


async def test_redis_legacy_scan_dedup_and_order_before_display_paging():
    store = ConversationStore(
        {
            "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
            "UPSTASH_REDIS_REST_TOKEN": "private",
        }
    )
    a = {"sequence": 1, "message_id": "a"}
    b = {"sequence": 2, "message_id": "b"}
    store.command = AsyncMock(
        side_effect=[["4", ["b", json.dumps(b)]], ["0", ["a", json.dumps(a), "b", json.dumps(b)]]]
    )
    assert [m["message_id"] for m in await store.messages()] == ["a", "b"]


async def test_desktop_history_filters_actor_source_and_task_and_restores_redis_only(app):
    def message(mid, source, actor, sequence, mode="Issue"):
        return {
            "message_id": mid,
            "conversation_id": "c",
            "task_id": "task-" + mid,
            "source": source,
            "actor_id": actor,
            "role": "user",
            "mode": mode,
            "sequence": sequence,
            "created_at": sequence,
            "content": mid,
        }

    app.archive.messages.return_value = [
        message("a", "desktop", "local:me", 1),
        message("b", "telegram", "123:123:0", 2),
        message("c", "telegram", "123:999:0", 3),
        message("d", "desktop", "local:other", 4),
        message("e", "telegram", "123:123:0", 5, "Pull Request"),
    ]
    first = await app.desktop_history("local:me", "Issue", count=1)
    assert first["reset"] and [m["message_id"] for m in first["messages"]] == ["a"]
    second = await app.desktop_history("local:me", "Issue", first["cursor"], count=1)
    assert second["cursor"] == "0" and second["messages"][0]["message_id"] == "b"
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values=app.values)
    assert len((await reopened.desktop_history("local:me", "Issue"))["messages"]) == 2
    app.values["PRBOT_TELEGRAM_ACTOR_ID"] = ""
    assert len((await reopened.desktop_history("local:me", "Issue"))["messages"]) == 1


async def test_result_retry_preserves_timestamp_and_does_not_execute_sdk(app):
    await app.submit(
        "pending-task", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix"
    )
    app.change(
        "pending-task", "save_pending", json.dumps({"summary": "Done", "outcome": "completed"})
    )
    changed = app.request("pending-task")["changed"]
    await app.retry_save("pending-task", "local:me")
    assert app.request("pending-task")["changed"] == changed
    app.adapter.invoke.assert_not_awaited()
    assert app.archive.save.call_args.args[0]["created_at"] == changed
