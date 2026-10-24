"""Issue #15: real archive/read seams, exercised only by remote CI."""

from unittest.mock import AsyncMock, Mock

import pytest

from prbot.config import ConfigError
from prbot.conversation_service import ConversationService
from prbot.conversation_store import ConversationStore

VALUES = {
    "UPSTASH_REDIS_REST_URL": "https://history.upstash.io",
    "UPSTASH_REDIS_REST_TOKEN": "test-token",
}


def message(mode="Issue", number=1, actor="local:me", source="desktop"):
    return {
        "conversation_id": ConversationService.conversation(actor, source, mode),
        "message_id": f"message-{number}",
        "task_id": f"task-{number}",
        "actor_id": actor,
        "source": source,
        "mode": mode,
        "role": "user",
        "content": f"Saved conversation {number}\nSecond line",
        "sequence": number,
        "created_at": number,
    }


async def test_all_history_shows_issue_pr_and_private_without_foreign_messages():
    archive = Mock(messages=AsyncMock(return_value=[
        message(), message("Pull Request", 2), message("Chat History", 3),
        message(number=4, actor="local:other"),
    ]))
    app = ConversationService(archive=archive, values={})
    page = await app.desktop_history("local:me", "All")
    assert [m["mode"] for m in page["messages"]] == ["Issue", "Pull Request", "Chat History"]


async def test_saved_dialogue_survives_restart_and_remote_outage():
    store = ConversationStore(VALUES)
    store.command = AsyncMock(return_value=1)
    await store.save(message())
    reopened = ConversationStore(VALUES)
    reopened.command = AsyncMock(side_effect=ConfigError("Message not saved. Retry."))
    page = await ConversationService(archive=reopened, values=VALUES).desktop_history(
        "local:me", "Issue"
    )
    assert page["messages"][0]["content"] == "Saved conversation 1\nSecond line"
    assert page["cached"] is True
    assert "local" in page["warning"].lower()


async def test_failed_remote_write_is_not_displayed_as_saved():
    store = ConversationStore(VALUES)
    store.command = AsyncMock(side_effect=ConfigError("Message not saved. Retry."))
    with pytest.raises(ConfigError):
        await store.save(message())
    with pytest.raises(ConfigError):
        await store.messages()


async def test_session_filter_happens_before_pagination_and_all_sessions_stay_visible():
    one, two = message(number=1), message(number=2)
    reply = {**one, "message_id": "reply-1", "role": "assistant", "sequence": 3}
    archive = Mock(messages=AsyncMock(return_value=[one, two, reply]))
    app = ConversationService(archive=archive, values={})
    page = await app.desktop_history("local:me", "All", count=1, session="task-1")
    assert [s["id"] for s in page["sessions"]] == ["task-1", "task-2"]
    assert page["messages"] == [one]
    next_page = await app.desktop_history(
        "local:me", "All", page["cursor"], count=1, session="task-1"
    )
    assert next_page["messages"] == [reply] and next_page["cursor"] == "0"
    assert not (await app.desktop_history("local:me", "All", session="foreign"))["messages"]


async def test_local_cache_does_not_reuse_telegram_authorization(monkeypatch):
    from prbot.conversation_store import LocalHistoryArchive

    store = ConversationStore(VALUES)
    store.command = AsyncMock(return_value=1)
    await store.save(message())
    await store.save(message(number=2, actor="123:123:0", source="telegram"))
    monkeypatch.setattr(
        "prbot.conversation_service.require_secrets", Mock(side_effect=ConfigError("Offline"))
    )
    app = ConversationService.for_desktop_read()
    assert isinstance(app.archive, LocalHistoryArchive)
    page = await app.desktop_history("local:me", "All")
    assert page["cached"] and len(page["messages"]) == 1
    assert page["messages"][0]["source"] == "desktop"
    assert not (await app.desktop_history("local:other", "All"))["messages"]


async def test_cache_is_separated_by_redis_database_and_survives_token_rotation():
    store = ConversationStore(VALUES)
    store.command = AsyncMock(return_value=1)
    await store.save(message())
    rotated = ConversationStore({**VALUES, "UPSTASH_REDIS_REST_TOKEN": "rotated-token"})
    assert len(rotated.local_messages()) == 1
    other = ConversationStore({**VALUES, "UPSTASH_REDIS_REST_URL": "https://other.upstash.io"})
    assert not other.local_messages()


async def test_duplicate_remote_write_recovers_original_record_after_cache_loss():
    import json

    store = ConversationStore(VALUES)
    original = message()
    store.command = AsyncMock(side_effect=[0, json.dumps(original)])
    await store.save({**original, "content": "Changed retry must not replace original"})
    assert store.local_messages() == [original]


def test_database_created_without_cloud_and_existing_tasks_retained():
    from prbot.journal import initialize_journal, journal_db

    path = initialize_journal()
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    with journal_db() as db:
        db.execute("INSERT INTO requests(id,status) VALUES ('existing','running')")
    initialize_journal()
    with journal_db() as db:
        status = db.execute("SELECT status FROM requests WHERE id='existing'").fetchone()[0]
        assert status == "running"
