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
